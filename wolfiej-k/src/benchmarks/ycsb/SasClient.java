package site.ycsb.db;

import site.ycsb.ByteIterator;
import site.ycsb.DB;
import site.ycsb.DBException;
import site.ycsb.Status;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.Vector;

public class SasClient extends DB {

    private static native void init0(long capacityHint);
    private static native void cleanup0();
    private static native ByteBuffer readOpen0(byte[] key, long[] handleOut);
    private static native void readClose0(long handle);
    private static native void put0(byte[] key, byte[] value);

    static {
        String backend = System.getProperty("sas.backend",
            System.getenv().getOrDefault("SAS_BACKEND", "hp"));
        System.loadLibrary("sas_jni_" + backend);
    }

    private final long[] handleOut = new long[1];
    private long pendingHandle = 0;

    @Override
    public void init() throws DBException {
        long records = Long.parseLong(
            getProperties().getProperty("recordcount", "1000"));
        init0(records * 2);
    }

    @Override
    public void cleanup() throws DBException {
        drainPending();
        cleanup0();
    }

    @Override
    public Status read(String table, String key, Set<String> fields,
                       Map<String, ByteIterator> result) {
        drainPending();
        ByteBuffer payload = readOpen0(
            key.getBytes(StandardCharsets.UTF_8), handleOut);
        if (payload == null) {
            return Status.NOT_FOUND;
        }
        pendingHandle = handleOut[0];
        RecordCodec.decode(payload, fields, result);
        return Status.OK;
    }

    @Override
    public Status scan(String table, String startKey, int recordCount,
                       Set<String> fields,
                       Vector<HashMap<String, ByteIterator>> result) {
        return Status.NOT_IMPLEMENTED;
    }

    @Override
    public Status update(String table, String key,
                         Map<String, ByteIterator> values) {
        return write(key, values);
    }

    @Override
    public Status insert(String table, String key,
                         Map<String, ByteIterator> values) {
        return write(key, values);
    }

    @Override
    public Status delete(String table, String key) {
        return Status.NOT_IMPLEMENTED;
    }

    private Status write(String key, Map<String, ByteIterator> values) {
        drainPending();
        put0(key.getBytes(StandardCharsets.UTF_8),
             RecordCodec.Encoded.of(values).toByteArray());
        return Status.OK;
    }

    private void drainPending() {
        if (pendingHandle != 0) {
            readClose0(pendingHandle);
            pendingHandle = 0;
        }
    }
}
