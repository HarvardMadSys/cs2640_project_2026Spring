package benchmark.sketch

import org.apache.datasketches.memory.Memory
import org.apache.datasketches.theta.{SetOperation, Sketch, Sketches, UpdateSketch}
import org.apache.spark.sql.Encoder
import org.apache.spark.sql.Encoders
import org.apache.spark.sql.expressions.Aggregator

final class ThetaDistinctAggregator(lgK: Int) extends Aggregator[String, Array[Byte], Double] {
  override def zero: Array[Byte] =
    UpdateSketch.builder().setNominalEntries(1 << lgK).build().compact().toByteArray

  override def reduce(buffer: Array[Byte], value: String): Array[Byte] = {
    if (value == null) {
      buffer
    } else {
      val sketch = UpdateSketch.builder().setNominalEntries(1 << lgK).build()
      sketch.update(value)
      merge(buffer, sketch.compact().toByteArray)
    }
  }

  override def merge(left: Array[Byte], right: Array[Byte]): Array[Byte] = {
    val union = SetOperation.builder().setNominalEntries(1 << lgK).buildUnion()
    union.union(readSketch(left))
    union.union(readSketch(right))
    union.getResult.toByteArray
  }

  override def finish(reduction: Array[Byte]): Double =
    readSketch(reduction).getEstimate

  override def bufferEncoder: Encoder[Array[Byte]] =
    Encoders.BINARY

  override def outputEncoder: Encoder[Double] =
    Encoders.scalaDouble

  private def readSketch(bytes: Array[Byte]): Sketch =
    Sketches.wrapSketch(Memory.wrap(bytes))
}
