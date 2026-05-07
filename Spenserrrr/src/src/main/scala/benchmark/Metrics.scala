package benchmark

import org.apache.spark.sql.{DataFrame, Row, SparkSession}
import org.apache.spark.sql.functions._

object Metrics {
  final case class ApproximationSummary(
      numGroups: Long,
      exactCardinality: Double,
      approximateCardinality: Double,
      errorMean: Double,
      errorMedian: Double,
      errorP95: Double,
      errorMax: Double
  )

  def exactResult(
      dataset: String,
      query: QuerySpec,
      exactDf: DataFrame,
      inputRows: Long,
      runtimeStats: RuntimeStats
  ): BenchmarkResult = {
    if (query.groupColumns.isEmpty) {
      val exact = readCount(exactDf.first())
      BenchmarkResult(
        dataset = dataset,
        queryName = query.name,
        method = "exact_spark_sql",
        inputRows = inputRows,
        numGroups = 1L,
        exactCardinality = exact,
        approximateCardinality = exact,
        runtimeMs = runtimeStats.medianMs,
        runtimeMeanMs = runtimeStats.meanMs,
        runtimeMinMs = runtimeStats.minMs,
        runtimeMaxMs = runtimeStats.maxMs,
        runtimeStddevMs = runtimeStats.stddevMs,
        runtimeTrials = runtimeStats.trials,
        relativeErrorMean = 0.0,
        relativeErrorMedian = 0.0,
        relativeErrorP95 = 0.0,
        relativeErrorMax = 0.0,
        relativeSd = None,
        notes = "ground truth"
      )
    } else {
      val summary = groupedExactSummary(exactDf)
      BenchmarkResult(
        dataset = dataset,
        queryName = query.name,
        method = "exact_spark_sql",
        inputRows = inputRows,
        numGroups = summary.numGroups,
        exactCardinality = summary.exactCardinality,
        approximateCardinality = summary.exactCardinality,
        runtimeMs = runtimeStats.medianMs,
        runtimeMeanMs = runtimeStats.meanMs,
        runtimeMinMs = runtimeStats.minMs,
        runtimeMaxMs = runtimeStats.maxMs,
        runtimeStddevMs = runtimeStats.stddevMs,
        runtimeTrials = runtimeStats.trials,
        relativeErrorMean = 0.0,
        relativeErrorMedian = 0.0,
        relativeErrorP95 = 0.0,
        relativeErrorMax = 0.0,
        relativeSd = None,
        notes = "ground truth; exact_cardinality is sum of per-group cardinalities"
      )
    }
  }

  def approximateResult(
      spark: SparkSession,
      dataset: String,
      query: QuerySpec,
      method: String,
      exactDf: DataFrame,
      approxDf: DataFrame,
      inputRows: Long,
      runtimeStats: RuntimeStats,
      relativeSd: Option[Double],
      notes: String
  ): BenchmarkResult = {
    if (query.groupColumns.isEmpty) {
      val exact = readCount(exactDf.first())
      val approx = readCount(approxDf.first())
      val error = relativeError(approx, exact)
      BenchmarkResult(
        dataset = dataset,
        queryName = query.name,
        method = method,
        inputRows = inputRows,
        numGroups = 1L,
        exactCardinality = exact,
        approximateCardinality = approx,
        runtimeMs = runtimeStats.medianMs,
        runtimeMeanMs = runtimeStats.meanMs,
        runtimeMinMs = runtimeStats.minMs,
        runtimeMaxMs = runtimeStats.maxMs,
        runtimeStddevMs = runtimeStats.stddevMs,
        runtimeTrials = runtimeStats.trials,
        relativeErrorMean = error,
        relativeErrorMedian = error,
        relativeErrorP95 = error,
        relativeErrorMax = error,
        relativeSd = relativeSd,
        notes = notes
      )
    } else {
      val summary = summarizeApproximation(spark, query, exactDf, approxDf)
      BenchmarkResult(
        dataset = dataset,
        queryName = query.name,
        method = method,
        inputRows = inputRows,
        numGroups = summary.numGroups,
        exactCardinality = summary.exactCardinality,
        approximateCardinality = summary.approximateCardinality,
        runtimeMs = runtimeStats.medianMs,
        runtimeMeanMs = runtimeStats.meanMs,
        runtimeMinMs = runtimeStats.minMs,
        runtimeMaxMs = runtimeStats.maxMs,
        runtimeStddevMs = runtimeStats.stddevMs,
        runtimeTrials = runtimeStats.trials,
        relativeErrorMean = summary.errorMean,
        relativeErrorMedian = summary.errorMedian,
        relativeErrorP95 = summary.errorP95,
        relativeErrorMax = summary.errorMax,
        relativeSd = relativeSd,
        notes = notes
      )
    }
  }

  def summarizeApproximation(
      spark: SparkSession,
      query: QuerySpec,
      exactDf: DataFrame,
      approxDf: DataFrame
  ): ApproximationSummary = {
    if (query.groupColumns.isEmpty) {
      val exact = readCount(exactDf.first())
      val approx = readCount(approxDf.first())
      val error = relativeError(approx, exact)
      ApproximationSummary(
        numGroups = 1L,
        exactCardinality = exact,
        approximateCardinality = approx,
        errorMean = error,
        errorMedian = error,
        errorP95 = error,
        errorMax = error
      )
    } else {
      groupedApproxSummary(spark, query, exactDf, approxDf)
    }
  }

  private def groupedExactSummary(exactDf: DataFrame): ApproximationSummary = {
    val row = exactDf
      .agg(
        count(lit(1)).cast("long").as("num_groups"),
        sum(col("distinct_count")).cast("double").as("exact_cardinality")
      )
      .first()

    ApproximationSummary(
      numGroups = row.getAs[Long]("num_groups"),
      exactCardinality = row.getAs[Double]("exact_cardinality"),
      approximateCardinality = row.getAs[Double]("exact_cardinality"),
      errorMean = 0.0,
      errorMedian = 0.0,
      errorP95 = 0.0,
      errorMax = 0.0
    )
  }

  private def groupedApproxSummary(
      spark: SparkSession,
      query: QuerySpec,
      exactDf: DataFrame,
      approxDf: DataFrame
  ): ApproximationSummary = {
    import spark.implicits._

    val exactRenamed = exactDf.withColumnRenamed("distinct_count", "exact_count")
    val approxRenamed = approxDf.withColumnRenamed("distinct_count", "approx_count")
    // Compare errors per output group, then summarize the error distribution.
    val joined = exactRenamed
      .join(approxRenamed, query.groupColumns, "inner")
      .withColumn(
        "relative_error",
        when($"exact_count" === 0L, lit(0.0))
          .otherwise(abs($"approx_count".cast("double") - $"exact_count".cast("double")) / $"exact_count".cast("double"))
      )

    val row = joined
      .agg(
        count(lit(1)).cast("long").as("num_groups"),
        sum(col("exact_count")).cast("double").as("exact_cardinality"),
        sum(col("approx_count")).cast("double").as("approximate_cardinality"),
        avg(col("relative_error")).as("error_mean"),
        expr("percentile_approx(relative_error, 0.5)").as("error_median"),
        expr("percentile_approx(relative_error, 0.95)").as("error_p95"),
        max(col("relative_error")).as("error_max")
      )
      .first()

    ApproximationSummary(
      numGroups = row.getAs[Long]("num_groups"),
      exactCardinality = row.getAs[Double]("exact_cardinality"),
      approximateCardinality = row.getAs[Double]("approximate_cardinality"),
      errorMean = row.getAs[Double]("error_mean"),
      errorMedian = row.getAs[Double]("error_median"),
      errorP95 = row.getAs[Double]("error_p95"),
      errorMax = row.getAs[Double]("error_max")
    )
  }

  private def readCount(row: Row): Double =
    row.getAs[Number]("distinct_count").doubleValue()

  private def relativeError(approx: Double, exact: Double): Double =
    if (exact == 0.0) 0.0 else math.abs(approx - exact) / exact

}
