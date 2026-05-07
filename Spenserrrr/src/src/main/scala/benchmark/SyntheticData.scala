package benchmark

import org.apache.spark.sql.{Column, DataFrame, SparkSession}
import org.apache.spark.sql.functions._

object SyntheticData {
  val ViewName: String = "benchmark_input"

  def build(spark: SparkSession, config: BenchmarkConfig): DataFrame = {
    val safeGroups = math.max(config.groups, 1)
    val safeDistinctValues = math.max(config.distinctValues, 1L)
    val safePartitions = math.max(config.partitions, 1)
    val safeDays = math.max(config.days, 1)

    val base = spark
      .range(0L, config.rows, 1L, safePartitions)
      .toDF("id")

    config.syntheticWorkload.toLowerCase match {
      case "uniform" =>
        buildUniform(base, safeDistinctValues, safeGroups, safeDays)
      case "high_cardinality" | "high-cardinality" =>
        buildHighCardinality(base, safeDistinctValues, safeGroups, safeDays)
      case "many_groups" | "many-groups" =>
        buildManyGroups(base, safeDistinctValues, safeGroups, safeDays)
      case "skewed" =>
        buildSkewed(base, safeDistinctValues, safeGroups, safeDays)
      case other =>
        throw new IllegalArgumentException(s"Unsupported synthetic workload: $other")
    }
  }

  private def buildUniform(
      base: DataFrame,
      distinctValues: Long,
      groups: Int,
      days: Int
  ): DataFrame =
    selectCanonical(
      base,
      distinctId = pmod(xxhash64(col("id"), lit("key")), lit(distinctValues)),
      groupId = pmod(xxhash64(col("id"), lit("group")), lit(groups)),
      dayId = pmod(xxhash64(col("id"), lit("day")), lit(days))
    )

  private def buildHighCardinality(
      base: DataFrame,
      distinctValues: Long,
      groups: Int,
      days: Int
  ): DataFrame =
    selectCanonical(
      base,
      distinctId = pmod(xxhash64(col("id"), lit("high_key")), lit(distinctValues)),
      groupId = pmod(xxhash64(col("id"), lit("group")), lit(groups)),
      dayId = pmod(xxhash64(col("id"), lit("day")), lit(days))
    )

  private def buildManyGroups(
      base: DataFrame,
      distinctValues: Long,
      groups: Int,
      days: Int
  ): DataFrame =
    selectCanonical(
      base,
      distinctId = pmod(xxhash64(col("id"), lit("key")), lit(distinctValues)),
      groupId = pmod(xxhash64(col("id"), lit("many_groups")), lit(groups)),
      dayId = pmod(xxhash64(col("id"), lit("day")), lit(days))
    )

  private def buildSkewed(
      base: DataFrame,
      distinctValues: Long,
      groups: Int,
      days: Int
  ): DataFrame = {
    val hotGroups = math.max(groups / 20, 1)
    val warmGroups = math.max(groups / 5, 1)

    val groupId =
      when(pmod(xxhash64(col("id"), lit("skew_bucket")), lit(100)) < 80, pmod(xxhash64(col("id"), lit("hot_group")), lit(hotGroups)))
        .when(pmod(xxhash64(col("id"), lit("skew_bucket")), lit(100)) < 95, pmod(xxhash64(col("id"), lit("warm_group")), lit(warmGroups)))
        .otherwise(pmod(xxhash64(col("id"), lit("cold_group")), lit(groups)))

    val hotKeySpace = math.max(distinctValues / 20L, 1L)
    val distinctId =
      when(pmod(xxhash64(col("id"), lit("key_bucket")), lit(100)) < 70, pmod(xxhash64(col("id"), lit("hot_key")), lit(hotKeySpace)))
        .otherwise(pmod(xxhash64(col("id"), lit("cold_key")), lit(distinctValues)))

    selectCanonical(
      base,
      distinctId = distinctId,
      groupId = groupId,
      dayId = pmod(xxhash64(col("id"), lit("day")), lit(days))
    )
  }

  private def selectCanonical(
      base: DataFrame,
      distinctId: Column,
      groupId: Column,
      dayId: Column
  ): DataFrame =
    // Every workload exposes the same columns so query code stays shared.
    base.select(
      col("id"),
      concat(lit("key_"), distinctId.cast("string")).as("distinct_key"),
      concat(lit("group_"), groupId.cast("string")).as("group_id"),
      date_add(to_date(lit("2024-01-01")), dayId.cast("int")).as("date_bucket")
    )
}
