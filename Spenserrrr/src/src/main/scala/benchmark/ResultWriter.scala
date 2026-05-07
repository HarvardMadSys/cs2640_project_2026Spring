package benchmark

import java.nio.charset.StandardCharsets
import java.nio.file.{Files, Path, Paths}
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

object ResultWriter {
  private val TimestampFormat: DateTimeFormatter =
    DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss")

  def createRunDirectory(outputRoot: String): Path = {
    val runDirectory = nextRunDirectory(outputRoot)
    Files.createDirectories(runDirectory)
    runDirectory
  }

  def writeResults(results: Seq[BenchmarkResult], runDirectory: Path): Path = {
    val outputPath = runDirectory.resolve("benchmark_results.csv")
    val lines = BenchmarkResult.CsvHeader +: results.map(BenchmarkResult.toCsv)
    Files.write(outputPath, lines.mkString(System.lineSeparator()).getBytes(StandardCharsets.UTF_8))
    outputPath
  }

  private def nextRunDirectory(outputRoot: String): Path = {
    // Timestamped directories make long benchmark runs resumable and auditable.
    val timestamp = LocalDateTime.now().format(TimestampFormat)
    val root = Paths.get(outputRoot)
    var suffix = 0
    var candidate = root.resolve(timestamp)

    while (Files.exists(candidate)) {
      suffix += 1
      candidate = root.resolve(s"${timestamp}_$suffix")
    }

    candidate
  }
}
