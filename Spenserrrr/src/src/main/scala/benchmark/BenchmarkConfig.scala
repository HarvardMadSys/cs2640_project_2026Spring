package benchmark

final case class BenchmarkConfig(
    dataset: String = "synthetic",
    inputPath: String = "",
    maxRows: Long = 0L,
    syntheticWorkload: String = "uniform",
    rows: Long = 1000000L,
    distinctValues: Long = 100000L,
    groups: Int = 100,
    days: Int = 30,
    partitions: Int = 4,
    relativeSd: Double = 0.05,
    thetaLgK: Int = 12,
    hllLgK: Int = 12,
    warmupRuns: Int = 1,
    measurementRuns: Int = 3,
    cacheInput: Boolean = true,
    cacheSketchTable: Boolean = true,
    cacheMeasuredResults: Boolean = true,
    includeDirectUdaf: Boolean = true,
    outputRoot: String = "results"
)

object BenchmarkConfig {
  def parse(args: Array[String]): BenchmarkConfig = {
    val values = parseValues(args.toList)

    BenchmarkConfig(
      dataset = values.getOrElse("dataset", "synthetic"),
      inputPath = values.getOrElse("input", ""),
      maxRows = values.get("max-rows").map(_.toLong).getOrElse(0L),
      syntheticWorkload = values.getOrElse("synthetic-workload", values.getOrElse("workload", "uniform")),
      rows = values.get("rows").map(_.toLong).getOrElse(1000000L),
      distinctValues = values.get("distinct").map(_.toLong).getOrElse(100000L),
      groups = values.get("groups").map(_.toInt).getOrElse(100),
      days = values.get("days").map(_.toInt).getOrElse(30),
      partitions = values.get("partitions").map(_.toInt).getOrElse(4),
      relativeSd = values.get("relative-sd").map(_.toDouble).getOrElse(0.05),
      thetaLgK = values.get("theta-lg-k").map(_.toInt).getOrElse(12),
      hllLgK = values.get("hll-lg-k").map(_.toInt).getOrElse(12),
      warmupRuns = values.get("warmups").map(_.toInt).getOrElse(1),
      measurementRuns = values.get("runs").map(_.toInt).getOrElse(3),
      cacheInput = booleanValue(values, "cache-input", default = !values.contains("no-cache-input")),
      cacheSketchTable = booleanValue(values, "cache-sketch-table", default = !values.contains("no-cache-sketch-table")),
      cacheMeasuredResults = booleanValue(values, "cache-measured-results", default = !values.contains("no-cache-measured-results")),
      includeDirectUdaf = !values.contains("skip-udaf") && !values.contains("skip-direct-udaf"),
      outputRoot = values.getOrElse("output-root", values.getOrElse("output", "results"))
    )
  }

  // Accept both --flag value and bare boolean flags.
  private def booleanValue(values: Map[String, String], key: String, default: Boolean): Boolean =
    values.get(key).map(_.toLowerCase).map {
      case "true" | "1" | "yes" | "y" => true
      case "false" | "0" | "no" | "n" => false
      case other                       => throw new IllegalArgumentException(s"Invalid boolean value for --$key: $other")
    }.getOrElse(default)

  private def parseValues(args: List[String]): Map[String, String] = {
    args match {
      case Nil => Map.empty
      case flag :: value :: rest if flag.startsWith("--") && !value.startsWith("--") =>
        parseValues(rest) + (flag.drop(2) -> value)
      case flag :: rest if flag.startsWith("--") =>
        parseValues(rest) + (flag.drop(2) -> "true")
      case _ :: rest =>
        parseValues(rest)
    }
  }
}
