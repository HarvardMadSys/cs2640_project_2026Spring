package benchmark.sketch

import benchmark.QuerySpec
import org.apache.datasketches.hll.{HllSketch, TgtHllType, Union => HllUnion}
import org.apache.datasketches.memory.Memory
import org.apache.datasketches.theta.{SetOperation, Sketches, UpdateSketch}
import org.apache.spark.sql.{DataFrame, Row, SparkSession}
import org.apache.spark.sql.functions.col
import org.apache.spark.sql.types.{BinaryType, DoubleType, StructField, StructType}

import scala.collection.mutable

object PartitionedSketches {
  sealed trait SketchType
  case object Theta extends SketchType
  case object Hll extends SketchType

  private trait MutableSketch {
    def update(value: String): Unit
    def toBytes: Array[Byte]
  }

  def estimate(
      spark: SparkSession,
      input: DataFrame,
      query: QuerySpec,
      sketchType: SketchType,
      lgK: Int
  ): DataFrame = {
    if (query.groupColumns.isEmpty) {
      globalEstimate(spark, input, query.distinctColumn, sketchType, lgK)
    } else {
      groupedEstimate(spark, input, query, sketchType, lgK)
    }
  }

  def buildSketchTable(
      spark: SparkSession,
      input: DataFrame,
      groupColumns: Seq[String],
      distinctColumn: String,
      sketchType: SketchType,
      lgK: Int
  ): DataFrame = {
    require(groupColumns.nonEmpty, "Materialized sketch tables require at least one group column")

    val groupCount = groupColumns.length
    val selectedColumns = (groupColumns :+ distinctColumn).map(col)
    val outputSchema = StructType(groupColumns.map(name => input.schema(name)) :+ StructField("sketch_bytes", BinaryType, nullable = false))

    val partitionSketches = input
      .select(selectedColumns: _*)
      .rdd
      .mapPartitions { rows =>
        // Keep sketches mutable within a partition; only serialized bytes cross the shuffle.
        val sketchesByGroup = mutable.HashMap.empty[Vector[Any], MutableSketch]

        rows.foreach { row =>
          val key = (0 until groupCount).map(row.get).toVector
          val sketch = sketchesByGroup.getOrElseUpdate(key, newMutableSketch(sketchType, lgK))
          updateIfPresent(sketch, row.get(groupCount))
        }

        sketchesByGroup.iterator.map { case (key, sketch) =>
          key -> sketch.toBytes
        }
      }
      .reduceByKey { (left, right) =>
        mergeSketches(sketchType, lgK, left, right)
      }
      .map { case (key, bytes) =>
        Row.fromSeq(key :+ bytes)
      }

    spark.createDataFrame(partitionSketches, outputSchema)
  }

  def estimateFromSketchTable(
      spark: SparkSession,
      sketchTable: DataFrame,
      targetGroupColumns: Seq[String],
      sketchType: SketchType,
      lgK: Int
  ): DataFrame = {
    if (targetGroupColumns.isEmpty) {
      estimateGlobalFromSketchTable(spark, sketchTable, sketchType, lgK)
    } else {
      estimateGroupedFromSketchTable(spark, sketchTable, targetGroupColumns, sketchType, lgK)
    }
  }

  private def globalEstimate(
      spark: SparkSession,
      input: DataFrame,
      distinctColumn: String,
      sketchType: SketchType,
      lgK: Int
  ): DataFrame = {
    import spark.implicits._

    val partitionSketches = input
      .select(col(distinctColumn))
      .rdd
      .mapPartitions { rows =>
        val sketch = newMutableSketch(sketchType, lgK)
        rows.foreach { row =>
          updateIfPresent(sketch, row.get(0))
        }
        Iterator(sketch.toBytes)
      }

    val finalBytes = partitionSketches.fold(emptySketch(sketchType, lgK)) { (left, right) =>
      mergeSketches(sketchType, lgK, left, right)
    }

    Seq(Tuple1(estimateSketch(sketchType, finalBytes))).toDF("distinct_count")
  }

  private def groupedEstimate(
      spark: SparkSession,
      input: DataFrame,
      query: QuerySpec,
      sketchType: SketchType,
      lgK: Int
  ): DataFrame = {
    val groupCount = query.groupColumns.length
    val selectedColumns = (query.groupColumns :+ query.distinctColumn).map(col)
    val groupSchema = query.groupColumns.map(name => input.schema(name))
    val outputSchema = StructType(groupSchema :+ StructField("distinct_count", DoubleType, nullable = false))

    val partitionSketches = input
      .select(selectedColumns: _*)
      .rdd
      .mapPartitions { rows =>
        // Same partition-local path as materialization, but estimates immediately.
        val sketchesByGroup = mutable.HashMap.empty[Vector[Any], MutableSketch]

        rows.foreach { row =>
          val key = (0 until groupCount).map(row.get).toVector
          val sketch = sketchesByGroup.getOrElseUpdate(key, newMutableSketch(sketchType, lgK))
          updateIfPresent(sketch, row.get(groupCount))
        }

        sketchesByGroup.iterator.map { case (key, sketch) =>
          key -> sketch.toBytes
        }
      }

    val mergedSketches = partitionSketches.reduceByKey { (left, right) =>
      mergeSketches(sketchType, lgK, left, right)
    }

    val rows = mergedSketches.map { case (key, bytes) =>
      Row.fromSeq(key :+ estimateSketch(sketchType, bytes))
    }

    spark.createDataFrame(rows, outputSchema)
  }

  private def estimateGlobalFromSketchTable(
      spark: SparkSession,
      sketchTable: DataFrame,
      sketchType: SketchType,
      lgK: Int
  ): DataFrame = {
    import spark.implicits._

    val mergedBytes = sketchTable
      .select(col("sketch_bytes"))
      .rdd
      .map(row => row.getAs[Array[Byte]]("sketch_bytes"))
      .fold(emptySketch(sketchType, lgK)) { (left, right) =>
        mergeSketches(sketchType, lgK, left, right)
      }

    Seq(Tuple1(estimateSketch(sketchType, mergedBytes))).toDF("distinct_count")
  }

  private def estimateGroupedFromSketchTable(
      spark: SparkSession,
      sketchTable: DataFrame,
      targetGroupColumns: Seq[String],
      sketchType: SketchType,
      lgK: Int
  ): DataFrame = {
    val groupCount = targetGroupColumns.length
    val selectedColumns = (targetGroupColumns :+ "sketch_bytes").map(col)
    val outputSchema = StructType(targetGroupColumns.map(name => sketchTable.schema(name)) :+ StructField("distinct_count", DoubleType, nullable = false))

    val mergedSketches = sketchTable
      .select(selectedColumns: _*)
      .rdd
      .map { row =>
        val key = (0 until groupCount).map(row.get).toVector
        key -> row.getAs[Array[Byte]](groupCount)
      }
      // Roll up from the materialized grain to the requested query grain.
      .reduceByKey { (left, right) =>
        mergeSketches(sketchType, lgK, left, right)
      }

    val rows = mergedSketches.map { case (key, bytes) =>
      Row.fromSeq(key :+ estimateSketch(sketchType, bytes))
    }

    spark.createDataFrame(rows, outputSchema)
  }

  private def newMutableSketch(sketchType: SketchType, lgK: Int): MutableSketch =
    sketchType match {
      case Theta => new MutableThetaSketch(lgK)
      case Hll   => new MutableHllSketch(lgK)
    }

  private def updateIfPresent(sketch: MutableSketch, value: Any): Unit = {
    if (value != null) {
      sketch.update(value.toString)
    }
  }

  private def emptySketch(sketchType: SketchType, lgK: Int): Array[Byte] =
    newMutableSketch(sketchType, lgK).toBytes

  private def mergeSketches(sketchType: SketchType, lgK: Int, left: Array[Byte], right: Array[Byte]): Array[Byte] =
    sketchType match {
      case Theta =>
        val union = SetOperation.builder().setNominalEntries(1 << lgK).buildUnion()
        union.union(Sketches.wrapSketch(Memory.wrap(left)))
        union.union(Sketches.wrapSketch(Memory.wrap(right)))
        union.getResult.toByteArray
      case Hll =>
        val union = new HllUnion(lgK)
        union.update(HllSketch.heapify(Memory.wrap(left)))
        union.update(HllSketch.heapify(Memory.wrap(right)))
        union.getResult(TgtHllType.HLL_4).toCompactByteArray
    }

  private def estimateSketch(sketchType: SketchType, bytes: Array[Byte]): Double =
    sketchType match {
      case Theta => Sketches.wrapSketch(Memory.wrap(bytes)).getEstimate
      case Hll   => HllSketch.heapify(Memory.wrap(bytes)).getEstimate
    }

  private final class MutableThetaSketch(lgK: Int) extends MutableSketch {
    private val sketch = UpdateSketch.builder().setNominalEntries(1 << lgK).build()

    override def update(value: String): Unit =
      sketch.update(value)

    override def toBytes: Array[Byte] =
      sketch.compact().toByteArray
  }

  private final class MutableHllSketch(lgK: Int) extends MutableSketch {
    private val sketch = new HllSketch(lgK, TgtHllType.HLL_4)

    override def update(value: String): Unit =
      sketch.update(value)

    override def toBytes: Array[Byte] =
      sketch.toCompactByteArray
  }
}
