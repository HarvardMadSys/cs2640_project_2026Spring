package site.ycsb.db;

import site.ycsb.ByteIterator;
import site.ycsb.DB;
import site.ycsb.DBException;
import site.ycsb.Status;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
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
        deserialize(payload.order(ByteOrder.LITTLE_ENDIAN), fields, result);
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
        drainPending();
        put0(key.getBytes(StandardCharsets.UTF_8), serialize(values));
        return Status.OK;
    }

    @Override
    public Status insert(String table, String key,
                         Map<String, ByteIterator> values) {
        drainPending();
        put0(key.getBytes(StandardCharsets.UTF_8), serialize(values));
        return Status.OK;
    }

    @Override
    public Status delete(String table, String key) {
        return Status.NOT_IMPLEMENTED;
    }

    private void drainPending() {
        if (pendingHandle != 0) {
            readClose0(pendingHandle);
            pendingHandle = 0;
        }
    }

    private static byte[] serialize(Map<String, ByteIterator> values) {
        byte[][] keys = new byte[values.size()][];
        byte[][] vals = new byte[values.size()][];
        int total = 4;
        int i = 0;
        for (Map.Entry<String, ByteIterator> e : values.entrySet()) {
            keys[i] = e.getKey().getBytes(StandardCharsets.UTF_8);
            vals[i] = e.getValue().toArray();
            total += 4 + keys[i].length + 4 + vals[i].length;
            i++;
        }
        ByteBuffer buf = ByteBuffer.allocate(total).order(ByteOrder.LITTLE_ENDIAN);
        buf.putInt(values.size());
        for (int j = 0; j < keys.length; j++) {
            buf.putInt(keys[j].length).put(keys[j])
               .putInt(vals[j].length).put(vals[j]);
        }
        return buf.array();
    }

    private static void deserialize(ByteBuffer payload, Set<String> wanted,
                                    Map<String, ByteIterator> out) {
        int count = payload.getInt();
        for (int i = 0; i < count; i++) {
            int klen = payload.getInt();
            byte[] kbytes = new byte[klen];
            payload.get(kbytes);
            int vlen = payload.getInt();
            String field = new String(kbytes, StandardCharsets.UTF_8);
            if (wanted == null || wanted.contains(field)) {
                ByteBuffer view = payload.slice();
                view.limit(vlen);
                out.put(field, new ByteBufferByteIterator(view, vlen));
            }
            payload.position(payload.position() + vlen);
        }
    }

    private static final class ByteBufferByteIterator extends ByteIterator {
        private final ByteBuffer view;
        private final int length;
        private int pos;

        ByteBufferByteIterator(ByteBuffer view, int length) {
            this.view = view;
            this.length = length;
        }

        @Override
        public boolean hasNext() {
            return pos < length;
        }

        @Override
        public byte nextByte() {
            return view.get(pos++);
        }

        @Override
        public long bytesLeft() {
            return length - pos;
        }

        @Override
        public byte[] toArray() {
            int remaining = length - pos;
            byte[] out = new byte[remaining];
            ByteBuffer dup = view.duplicate();
            dup.position(pos);
            dup.get(out);
            pos = length;
            return out;
        }
    }
}
