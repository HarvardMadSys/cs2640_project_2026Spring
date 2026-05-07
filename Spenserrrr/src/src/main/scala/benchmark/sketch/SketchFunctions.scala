package benchmark.sketch

import org.apache.spark.sql.SparkSession
import org.apache.spark.sql.functions.udaf

object SketchFunctions {
  val ThetaFunctionName: String = "theta_distinct"
  val HllFunctionName: String = "hll_distinct"

  def register(spark: SparkSession, thetaLgK: Int, hllLgK: Int): Unit = {
    // Direct UDAFs are kept as a baseline against partition-level sketching.
    spark.udf.register(ThetaFunctionName, udaf(new ThetaDistinctAggregator(thetaLgK)))
    spark.udf.register(HllFunctionName, udaf(new HllDistinctAggregator(hllLgK)))
  }
}
