package benchmark

final case class QuerySpec(
    name: String,
    groupColumns: Seq[String],
    distinctColumn: String = "distinct_key"
) {
  // Generate the same logical query for each implementation.
  def exactSql(tableName: String): String =
    distinctSql(tableName, s"COUNT(DISTINCT $distinctColumn)")

  def approxSql(tableName: String, relativeSd: Double): String =
    distinctSql(tableName, s"approx_count_distinct($distinctColumn, $relativeSd)")

  def sketchSql(tableName: String, functionName: String): String =
    distinctSql(tableName, s"$functionName($distinctColumn)")

  private def distinctSql(tableName: String, aggregate: String): String = {
    if (groupColumns.isEmpty) {
      s"SELECT $aggregate AS distinct_count FROM $tableName"
    } else {
      val groups = groupColumns.mkString(", ")
      s"SELECT $groups, $aggregate AS distinct_count FROM $tableName GROUP BY $groups"
    }
  }
}

object BenchmarkQueries {
  // These cover no-group, single-group, time rollup, and materialized-grain queries.
  val AllQueries: Seq[QuerySpec] = Seq(
    QuerySpec(name = "global_distinct", groupColumns = Seq.empty),
    QuerySpec(name = "grouped_distinct", groupColumns = Seq("group_id")),
    QuerySpec(name = "time_window_distinct", groupColumns = Seq("date_bucket")),
    QuerySpec(name = "multi_group_distinct", groupColumns = Seq("date_bucket", "group_id"))
  )
}
