ThisBuild / organization := "benchmark"
ThisBuild / version := "0.1.0-SNAPSHOT"
ThisBuild / scalaVersion := "2.12.18"

lazy val root = (project in file("."))
  .settings(
    name := "datasketches-benchmark",
    libraryDependencies ++= Seq(
      "org.apache.spark" %% "spark-sql" % "3.5.1",
      "org.apache.datasketches" % "datasketches-java" % "6.1.1"
    ),
    Compile / mainClass := Some("benchmark.BenchmarkRunner"),
    run / fork := true,
    run / javaOptions ++= Seq(
      "-Xms1G",
      "-Xmx4G",
      "-Dspark.driver.bindAddress=127.0.0.1"
    )
  )
