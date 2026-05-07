package benchmark.data

import benchmark.BenchmarkConfig
import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._

object TaxiData {
  def load(spark: SparkSession, config: BenchmarkConfig): DataFrame = {
    require(config.inputPath.nonEmpty, "--input is required when --dataset taxi")

    val raw = spark.read.parquet(config.inputPath)
    val cleaned = raw
      .select(
        col("tpep_pickup_datetime").as("pickup_ts"),
        col("PULocationID").cast("int").as("pickup_location_id"),
        col("DOLocationID").cast("int").as("dropoff_location_id")
      )
      .where(
        // Keep only rows that can form a stable route key and time bucket.
        col("pickup_ts").isNotNull &&
          col("pickup_location_id").isNotNull &&
          col("dropoff_location_id").isNotNull &&
          col("pickup_location_id") > 0 &&
          col("dropoff_location_id") > 0
      )
      .select(
        // A pickup/dropoff route acts like a user-level distinct key.
        concat(
          lit("route_"),
          col("pickup_location_id").cast("string"),
          lit("_"),
          col("dropoff_location_id").cast("string")
        ).as("distinct_key"),
        concat(lit("pickup_"), col("pickup_location_id").cast("string")).as("group_id"),
        to_date(col("pickup_ts")).as("date_bucket")
      )
      .where(col("date_bucket").isNotNull)

    if (config.maxRows > 0) cleaned.limit(math.min(config.maxRows, Int.MaxValue.toLong).toInt) else cleaned
  }
}
