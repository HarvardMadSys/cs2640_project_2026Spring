package benchmark

import benchmark.data.DatasetLoader
import benchmark.materialization.MaterializationBenchmark
import benchmark.sketch.{PartitionedSketches, SketchFunctions}
import org.apache.spark.sql.{DataFrame, SparkSession}

object BenchmarkRunner {
  private final case class ApproxMethod(
      name: String,
      run: (SparkSession, DataFrame, QuerySpec) => (DataFrame, RuntimeStats),
      relativeSd: Option[Double],
      notes: String
  )

  def main(args: Array[String]): Unit = {
    val config = BenchmarkConfig.parse(args)
    val localParallelism = math.max(config.partitions, 1)

    val spark = SparkSession
      .builder()
      .appName("DataSketches Benchmark Baseline")
      .master(s"local[$localParallelism]")
      .config("spark.sql.shuffle.partitions", localParallelism.toString)
      .config("spark.driver.bindAddress", "127.0.0.1")
      .getOrCreate()

    spark.sparkContext.setLogLevel("WARN")
    SketchFunctions.register(spark, config.thetaLgK, config.hllLgK)

    try {
      val runDirectory = ResultWriter.createRunDirectory(config.outputRoot)
      // Force both synthetic and real inputs through Parquet so scan costs are comparable.
      val loadedInput = DatasetLoader.loadAndMaterialize(spark, config, runDirectory)
      val input = if (config.cacheInput) loadedInput.cache() else loadedInput
      val inputRows = input.count()
      input.createOrReplaceTempView(DatasetLoader.ViewName)

      val results = BenchmarkQueries.AllQueries.flatMap { query =>
        runQueryPair(spark, config, input, query, inputRows)
      }

      val outputPath = ResultWriter.writeResults(results, runDirectory)
      println(s"Wrote ${results.size} benchmark rows to $outputPath")

      val materializationOutputPath = MaterializationBenchmark.run(
        spark = spark,
        config = config,
        input = input,
        inputRows = inputRows,
        exactQueries = exactQueries(spark, config),
        queries = BenchmarkQueries.AllQueries,
        runDirectory = runDirectory
      )
      println(s"Wrote materialization results to $materializationOutputPath")
    } finally {
      spark.stop()
    }
  }

  private def runQueryPair(
      spark: SparkSession,
      config: BenchmarkConfig,
      input: DataFrame,
      query: QuerySpec,
      inputRows: Long
  ): Seq[BenchmarkResult] = {
    println(s"Running ${query.name}")

    val (exactDf, exactRuntimeStats) =
      timedQuery(spark, config, query.exactSql(DatasetLoader.ViewName))

    val exactResult = Metrics.exactResult(config.dataset, query, exactDf, inputRows, exactRuntimeStats)
    val approximateResults = approximateMethods(config).map { method =>
      val (approxDf, approxRuntimeStats) = method.run(spark, input, query)
      Metrics.approximateResult(
        spark = spark,
        dataset = config.dataset,
        query = query,
        method = method.name,
        exactDf = exactDf,
        approxDf = approxDf,
        inputRows = inputRows,
        runtimeStats = approxRuntimeStats,
        relativeSd = method.relativeSd,
        notes = method.notes
      )
    }

    exactResult +: approximateResults
  }

  private def approximateMethods(config: BenchmarkConfig): Seq[ApproxMethod] = {
    val directUdafMethods =
      if (config.includeDirectUdaf) {
        Seq(
          ApproxMethod(
            name = "datasketches_theta_udaf",
            run = (spark, _, query) => timedQuery(spark, config, query.sketchSql(DatasetLoader.ViewName, SketchFunctions.ThetaFunctionName)),
            relativeSd = None,
            notes = s"Apache DataSketches Theta direct UDAF; lgK=${config.thetaLgK}"
          ),
          ApproxMethod(
            name = "datasketches_hll_udaf",
            run = (spark, _, query) => timedQuery(spark, config, query.sketchSql(DatasetLoader.ViewName, SketchFunctions.HllFunctionName)),
            relativeSd = None,
            notes = s"Apache DataSketches HLL direct UDAF; lgK=${config.hllLgK}"
          )
        )
      } else {
        Seq.empty
      }

    Seq(
      ApproxMethod(
        name = "spark_approx_count_distinct",
        run = (spark, _, query) => timedQuery(spark, config, query.approxSql(DatasetLoader.ViewName, config.relativeSd)),
        relativeSd = Some(config.relativeSd),
        notes = "Spark built-in HLL++ approximation"
      ),
      ApproxMethod(
        name = "datasketches_theta_partitioned",
        run = (spark, input, query) =>
          timedDataFrame(config)(PartitionedSketches.estimate(spark, input, query, PartitionedSketches.Theta, config.thetaLgK)),
        relativeSd = None,
        notes = s"Apache DataSketches Theta partition-level sketching; lgK=${config.thetaLgK}"
      ),
      ApproxMethod(
        name = "datasketches_hll_partitioned",
        run = (spark, input, query) =>
          timedDataFrame(config)(PartitionedSketches.estimate(spark, input, query, PartitionedSketches.Hll, config.hllLgK)),
        relativeSd = None,
        notes = s"Apache DataSketches HLL partition-level sketching; lgK=${config.hllLgK}"
      )
    ) ++ directUdafMethods
  }

  private def exactQueries(spark: SparkSession, config: BenchmarkConfig): Map[String, (DataFrame, RuntimeStats)] =
    BenchmarkQueries.AllQueries.map { query =>
      query.name -> timedQuery(spark, config, query.exactSql(DatasetLoader.ViewName))
    }.toMap

  private def timedQuery(spark: SparkSession, config: BenchmarkConfig, sqlText: String): (DataFrame, RuntimeStats) = {
    timedDataFrame(config)(spark.sql(sqlText))
  }

  private def timedDataFrame(config: BenchmarkConfig)(df: => DataFrame): (DataFrame, RuntimeStats) = {
    timeDataFrame(df, config.warmupRuns, config.measurementRuns, config.cacheMeasuredResults)
  }

  private def timeDataFrame(df: => DataFrame, warmups: Int, trials: Int, cacheResult: Boolean): (DataFrame, RuntimeStats) = {
    val safeWarmups = math.max(warmups, 0)
    val safeTrials = math.max(trials, 1)

    (0 until safeWarmups).foreach { _ =>
      df.count()
    }

    val samples = (0 until safeTrials).map { _ =>
      val startNs = System.nanoTime()
      df.count()
      (System.nanoTime() - startNs) / 1000000L
    }

    val computed = df
    if (cacheResult) {
      computed.cache()
      computed.count()
    }
    (computed, RuntimeStats.from(samples))
  }
}
