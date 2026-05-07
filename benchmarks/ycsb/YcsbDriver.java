package site.ycsb.db;

import site.ycsb.DB;
import site.ycsb.Workload;
import site.ycsb.measurements.Measurements;

import java.io.File;
import java.io.FileReader;
import java.lang.reflect.Constructor;
import java.util.Properties;
import java.util.concurrent.TimeUnit;

public final class YcsbDriver {

    private static final String LOAD_BARRIER_PREFIX = "/tmp/ycsb_load_done_";

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

        props.setProperty("insertstart", "0");
        props.setProperty("insertcount", String.valueOf(perProcRecords));
        props.setProperty("recordcount", String.valueOf(perProcRecords));

        String workloadClass = props.getProperty(
            "workload", "site.ycsb.workloads.CoreWorkload");
        String dbClassName = props.getProperty(
            "dbclass", "site.ycsb.db.SasClient");
        Constructor<? extends DB> dbCtor =
            Class.forName(dbClassName).asSubclass(DB.class).getDeclaredConstructor();

        Workload workload = (Workload) Class.forName(workloadClass)
            .getDeclaredConstructor().newInstance();
        workload.init(props);

        runPhase("load", threads, props, workload, dbCtor, perProcRecords, true);
        loadBarrier(procid, nprocs);
        runPhase("run", threads, props, workload, dbCtor, perProcOps, false);

        workload.cleanup();
    }

    private static void loadBarrier(int procid, int nprocs) throws Exception {
        if (nprocs <= 1) {
            return;
        }
        new File(LOAD_BARRIER_PREFIX + procid).createNewFile();
        long deadlineNs = System.nanoTime()
                          + TimeUnit.MINUTES.toNanos(5);
        while (true) {
            int count = 0;
            for (int i = 0; i < nprocs; i++) {
                if (new File(LOAD_BARRIER_PREFIX + i).exists()) {
                    count++;
                }
            }
            if (count == nprocs) {
                return;
            }
            if (System.nanoTime() > deadlineNs) {
                throw new RuntimeException(
                    "load barrier timeout: " + count + "/" + nprocs);
            }
            Thread.sleep(50);
        }
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
