package site.ycsb.db;

import site.ycsb.DB;
import site.ycsb.Workload;
import site.ycsb.measurements.Measurements;

import java.io.FileReader;
import java.lang.reflect.Constructor;
import java.util.Properties;

public final class YcsbDriver {

    public static void main(String[] args) throws Exception {
        Properties props = new Properties();
        int threads = 1;
        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "-P":
                    try (FileReader r = new FileReader(args[++i])) {
                        props.load(r);
                    }
                    break;
                case "-p":
                    String[] kv = args[++i].split("=", 2);
                    props.setProperty(kv[0], kv[1]);
                    break;
                case "-threads":
                    threads = Integer.parseInt(args[++i]);
                    break;
                default:
                    throw new IllegalArgumentException("unknown arg: " + args[i]);
            }
        }
        props.setProperty("writeallfields", "true");
        Measurements.setProperties(props);

        long records = Long.parseLong(props.getProperty("recordcount", "1000"));
        long ops = Long.parseLong(props.getProperty("operationcount", "1000"));
        int procid = Integer.parseInt(props.getProperty("procid", "0"));
        int nprocs = Integer.parseInt(props.getProperty("nprocs", "1"));
        long perProcRecords = records / nprocs;
        long perProcOps = ops / nprocs;
        long loadStart = procid * perProcRecords;

        String workloadClass = props.getProperty(
            "workload", "site.ycsb.workloads.CoreWorkload");
        String dbClassName = props.getProperty(
            "dbclass", "site.ycsb.db.SasClient");
        Constructor<? extends DB> dbCtor =
            Class.forName(dbClassName).asSubclass(DB.class).getDeclaredConstructor();

        Properties loadProps = new Properties();
        loadProps.putAll(props);
        loadProps.setProperty("insertstart", String.valueOf(loadStart));
        loadProps.setProperty("insertcount", String.valueOf(perProcRecords));
        Workload loadWorkload = (Workload) Class.forName(workloadClass)
            .getDeclaredConstructor().newInstance();
        loadWorkload.init(loadProps);

        Properties runProps = new Properties();
        runProps.putAll(props);
        runProps.setProperty("insertstart", String.valueOf(loadStart));
        runProps.setProperty("insertcount", String.valueOf(perProcRecords));
        Workload runWorkload = (Workload) Class.forName(workloadClass)
            .getDeclaredConstructor().newInstance();
        runWorkload.init(runProps);

        runPhase("load", threads, loadProps, loadWorkload, dbCtor,
                 perProcRecords, true);
        runPhase("run",  threads, runProps,  runWorkload,  dbCtor,
                 perProcOps,    false);

        loadWorkload.cleanup();
        runWorkload.cleanup();
    }

    private static void runPhase(String name, int threads, Properties props,
                                 Workload workload,
                                 Constructor<? extends DB> dbCtor,
                                 long total, boolean insert)
        throws InterruptedException {
        long perThread = total / threads;
        long remainder = total - perThread * threads;
        Thread[] ts = new Thread[threads];
        long start = System.nanoTime();
        for (int i = 0; i < threads; i++) {
            int tid = i;
            long myOps = perThread + (i == 0 ? remainder : 0);
            ts[i] = new Thread(() -> {
                try {
                    DB db = dbCtor.newInstance();
                    db.setProperties(props);
                    db.init();
                    Object state = workload.initThread(props, tid, threads);
                    for (long j = 0; j < myOps; j++) {
                        if (insert) {
                            workload.doInsert(db, state);
                        } else {
                            workload.doTransaction(db, state);
                        }
                    }
                    db.cleanup();
                } catch (Exception e) {
                    e.printStackTrace();
                }
            });
            ts[i].start();
        }
        for (Thread t : ts) {
            t.join();
        }
        long elapsedNs = System.nanoTime() - start;
        double mops = total * 1000.0 / elapsedNs;
        System.out.printf("%-5s threads=%-3d ops=%-9d %.3f Mops/s (%.3f s)%n",
            name, threads, total, mops, elapsedNs / 1e9);
    }
}
