package benchmark.materialization

import java.nio.charset.StandardCharsets
import java.nio.file.{Files, Path}

import benchmark.sketch.PartitionedSketches
import benchmark.{BenchmarkConfig, Metrics, QuerySpec, RuntimeStats}
import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._

object MaterializationBenchmark {
  // Store sketches at the finest grain needed by all benchmark query rollups.
  private val MaterializationGroup: Seq[String] = Seq("date_bucket", "group_id")

  private final case class SketchConfig(
      name: String,
      sketchType: PartitionedSketches.SketchType,
      lgK: Int
  )

  def run(
      spark: SparkSession,
      config: BenchmarkConfig,
      input: DataFrame,
      inputRows: Long,
      exactQueries: Map[String, (DataFrame, RuntimeStats)],
      queries: Seq[QuerySpec],
      runDirectory: Path
  ): Path = {
    val materializedRoot = runDirectory.resolve("materialized_sketches")
    Files.createDirectories(materializedRoot)
    val rawInputSizeBytes = directorySizeBytes(runDirectory.resolve("data").resolve(s"${config.dataset}.parquet"))

    val sketchConfigs = Seq(
      SketchConfig("theta", PartitionedSketches.Theta, config.thetaLgK),
      SketchConfig("hll", PartitionedSketches.Hll, config.hllLgK)
    )

    val results = sketchConfigs.flatMap { sketchConfig =>
      runForSketch(spark, config, input, inputRows, exactQueries, queries, materializedRoot, rawInputSizeBytes, sketchConfig)
    }

    val outputPath = runDirectory.resolve("materialization_results.csv")
    val lines = MaterializationResult.CsvHeader +: results.map(MaterializationResult.toCsv)
    Files.write(outputPath, lines.mkString(System.lineSeparator()).getBytes(StandardCharsets.UTF_8))
    outputPath
  }

  private def runForSketch(
      spark: SparkSession,
      config: BenchmarkConfig,
      input: DataFrame,
      inputRows: Long,
      exactQueries: Map[String, (DataFrame, RuntimeStats)],
      queries: Seq[QuerySpec],
      materializedRoot: Path,
      rawInputSizeBytes: Long,
      sketchConfig: SketchConfig
  ): Seq[MaterializationResult] = {
    val sketchDir = materializedRoot.resolve(sketchConfig.name)

    val (sketchTable, buildStats) = timedDataFrame(config, cacheResult = true) {
      PartitionedSketches.buildSketchTable(
        spark = spark,
        input = input,
        groupColumns = MaterializationGroup,
        distinctColumn = "distinct_key",
        sketchType = sketchConfig.sketchType,
        lgK = sketchConfig.lgK
      )
    }

    sketchTable.write.mode("overwrite").parquet(sketchDir.toString)
    val loadedSketchTable = spark.read.parquet(sketchDir.toString)
    val storedSketchTable = if (config.cacheSketchTable) loadedSketchTable.cache() else loadedSketchTable
    val numSketchRows = storedSketchTable.count()
    val sketchTableSizeBytes = directorySizeBytes(sketchDir)
    val rawToSketchSizeRatio =
      if (sketchTableSizeBytes == 0L) 0.0 else rawInputSizeBytes.toDouble / sketchTableSizeBytes.toDouble
    val averageSketchSizeBytes =
      storedSketchTable.agg(avg(length(col("sketch_bytes")).cast("double")).as("avg_sketch_bytes")).first().getAs[Double]("avg_sketch_bytes")

    queries.map { query =>
      val (exactDf, exactRuntimeStats) = exactQueries(query.name)
      val (estimateDf, sketchQueryStats) = timedDataFrame(config, cacheResult = config.cacheMeasuredResults) {
        PartitionedSketches.estimateFromSketchTable(
          spark = spark,
          sketchTable = storedSketchTable,
          targetGroupColumns = query.groupColumns,
          sketchType = sketchConfig.sketchType,
          lgK = sketchConfig.lgK
        )
      }

      val summary = Metrics.summarizeApproximation(spark, query, exactDf, estimateDf)
      val breakEven = breakEvenQueries(buildStats.medianMs, exactRuntimeStats.medianMs, sketchQueryStats.medianMs)
      val workload5ExactMs = workloadExact(5, exactRuntimeStats)
      val workload5SketchMs = workloadSketch(5, buildStats, sketchQueryStats)
      val workload10ExactMs = workloadExact(10, exactRuntimeStats)
      val workload10SketchMs = workloadSketch(10, buildStats, sketchQueryStats)

      MaterializationResult(
        dataset = config.dataset,
        sketchType = sketchConfig.name,
        materializationGroup = MaterializationGroup.mkString("+"),
        queryName = query.name,
        inputRows = inputRows,
        numSketchRows = numSketchRows,
        exactCardinality = summary.exactCardinality,
        approximateCardinality = summary.approximateCardinality,
        buildTimeMs = buildStats.medianMs,
        buildMeanMs = buildStats.meanMs,
        buildMinMs = buildStats.minMs,
        buildMaxMs = buildStats.maxMs,
        buildStddevMs = buildStats.stddevMs,
        exactQueryTimeMs = exactRuntimeStats.medianMs,
        exactQueryMeanMs = exactRuntimeStats.meanMs,
        exactQueryMinMs = exactRuntimeStats.minMs,
        exactQueryMaxMs = exactRuntimeStats.maxMs,
        exactQueryStddevMs = exactRuntimeStats.stddevMs,
        sketchQueryTimeMs = sketchQueryStats.medianMs,
        sketchQueryMeanMs = sketchQueryStats.meanMs,
        sketchQueryMinMs = sketchQueryStats.minMs,
        sketchQueryMaxMs = sketchQueryStats.maxMs,
        sketchQueryStddevMs = sketchQueryStats.stddevMs,
        sketchTableSizeBytes = sketchTableSizeBytes,
        averageSketchSizeBytes = averageSketchSizeBytes,
        rawInputSizeBytes = rawInputSizeBytes,
        rawToSketchSizeRatio = rawToSketchSizeRatio,
        workload5ExactMs = workload5ExactMs,
        workload5SketchMs = workload5SketchMs,
        workload10ExactMs = workload10ExactMs,
        workload10SketchMs = workload10SketchMs,
        runtimeTrials = sketchQueryStats.trials,
        relativeErrorMean = summary.errorMean,
        relativeErrorMedian = summary.errorMedian,
        relativeErrorP95 = summary.errorP95,
        relativeErrorMax = summary.errorMax,
        breakEvenQueries = breakEven,
        notes = "Materialized sketches built once at date_bucket+group_id and reused by merging stored sketch bytes"
      )
    }
  }

  private def timedDataFrame(config: BenchmarkConfig, cacheResult: Boolean)(df: => DataFrame): (DataFrame, RuntimeStats) = {
    timeDataFrame(df, config.warmupRuns, config.measurementRuns, cacheResult)
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

  private def breakEvenQueries(buildTimeMs: Long, exactQueryTimeMs: Long, sketchQueryTimeMs: Long): String = {
    val savedPerQueryMs = exactQueryTimeMs - sketchQueryTimeMs
    if (savedPerQueryMs <= 0) {
      "never"
    } else {
      // Number of repeated queries needed to pay back the one-time build.
      f"${buildTimeMs.toDouble / savedPerQueryMs.toDouble}%.2f"
    }
  }

  private def workloadExact(repeatedQueries: Int, exactStats: RuntimeStats): Long =
    repeatedQueries.toLong * exactStats.medianMs

  private def workloadSketch(repeatedQueries: Int, buildStats: RuntimeStats, sketchStats: RuntimeStats): Long =
    buildStats.medianMs + repeatedQueries.toLong * sketchStats.medianMs

  private def directorySizeBytes(path: Path): Long = {
    if (!Files.exists(path)) {
      0L
    } else {
      val stream = Files.walk(path)
      try {
        val iterator = stream.iterator()
        var total = 0L
        while (iterator.hasNext) {
          val current = iterator.next()
          if (Files.isRegularFile(current)) {
            total += Files.size(current)
          }
        }
        total
      } finally {
        stream.close()
      }
    }
  }
}
