package benchmark.sketch

import org.apache.datasketches.hll.{HllSketch, TgtHllType, Union}
import org.apache.datasketches.memory.Memory
import org.apache.spark.sql.Encoder
import org.apache.spark.sql.Encoders
import org.apache.spark.sql.expressions.Aggregator

final class HllDistinctAggregator(lgK: Int) extends Aggregator[String, Array[Byte], Double] {
  override def zero: Array[Byte] =
    new HllSketch(lgK, TgtHllType.HLL_4).toCompactByteArray

  override def reduce(buffer: Array[Byte], value: String): Array[Byte] = {
    if (value == null) {
      buffer
    } else {
      val union = new Union(lgK)
      union.update(readSketch(buffer))
      val update = new HllSketch(lgK, TgtHllType.HLL_4)
      update.update(value)
      union.update(update)
      union.getResult(TgtHllType.HLL_4).toCompactByteArray
    }
  }

  override def merge(left: Array[Byte], right: Array[Byte]): Array[Byte] = {
    val union = new Union(lgK)
    union.update(readSketch(left))
    union.update(readSketch(right))
    union.getResult(TgtHllType.HLL_4).toCompactByteArray
  }

  override def finish(reduction: Array[Byte]): Double =
    readSketch(reduction).getEstimate

  override def bufferEncoder: Encoder[Array[Byte]] =
    Encoders.BINARY

  override def outputEncoder: Encoder[Double] =
    Encoders.scalaDouble

  private def readSketch(bytes: Array[Byte]): HllSketch =
    HllSketch.heapify(Memory.wrap(bytes))
}
