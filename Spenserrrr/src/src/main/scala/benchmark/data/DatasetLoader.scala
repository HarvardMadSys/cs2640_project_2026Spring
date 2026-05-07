package benchmark.data

import java.nio.file.Path

import benchmark.{BenchmarkConfig, SyntheticData}
import org.apache.spark.sql.{DataFrame, SparkSession}

object DatasetLoader {
  val ViewName: String = "benchmark_input"

  def loadAndMaterialize(
      spark: SparkSession,
      config: BenchmarkConfig,
      runDirectory: Path
  ): DataFrame = {
    val dataset = config.dataset.toLowerCase
    val loaded = dataset match {
      case "synthetic" => SyntheticData.build(spark, config)
      case "taxi"      => TaxiData.load(spark, config)
      case other       => throw new IllegalArgumentException(s"Unsupported dataset: $other")
    }

    // Persist once so all methods benchmark the same physical input layout.
    val outputPath = runDirectory.resolve("data").resolve(s"$dataset.parquet").toString
    loaded.write.mode("overwrite").parquet(outputPath)
    spark.read.parquet(outputPath)
  }
}
