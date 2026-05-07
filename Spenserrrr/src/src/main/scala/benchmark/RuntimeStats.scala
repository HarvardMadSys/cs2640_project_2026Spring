package benchmark

final case class RuntimeStats(
    medianMs: Long,
    meanMs: Double,
    minMs: Long,
    maxMs: Long,
    stddevMs: Double,
    trials: Int
)

object RuntimeStats {
  def from(samples: Seq[Long]): RuntimeStats = {
    require(samples.nonEmpty, "RuntimeStats requires at least one sample")

    val sorted = samples.sorted
    val size = sorted.size
    // Median is the headline number because local Spark runs can have outliers.
    val median =
      if (size % 2 == 1) {
        sorted(size / 2)
      } else {
        math.round((sorted(size / 2 - 1).toDouble + sorted(size / 2).toDouble) / 2.0)
      }
    val mean = samples.sum.toDouble / size.toDouble
    val variance = samples.map(sample => math.pow(sample.toDouble - mean, 2.0)).sum / size.toDouble

    RuntimeStats(
      medianMs = median,
      meanMs = mean,
      minMs = sorted.head,
      maxMs = sorted.last,
      stddevMs = math.sqrt(variance),
      trials = size
    )
  }
}
