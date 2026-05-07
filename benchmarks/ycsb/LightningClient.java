package site.ycsb.db;

import jlightning.JlightningClient;
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

public class LightningClient extends DB {

    private static volatile JlightningClient sharedClient;
    private static final Object initLock = new Object();

    private static final int ID_BITS = 56;
    private static final long ID_MASK = (1L << ID_BITS) - 1;

    private JlightningClient client;
    private long pendingId = -1;
    private long idPrefix;

    private String[] fieldsBuf;
    private Object[] valsBuf;
    private byte[][] valBytesBuf;

    @Override
    public void init() throws DBException {
        if (sharedClient == null) {
            synchronized (initLock) {
                if (sharedClient == null) {
                    String socket = getProperties().getProperty(
                        "lightning.socket", "/tmp/lightning");
                    String password = getProperties().getProperty(
                        "lightning.password", "password");
                    sharedClient = new JlightningClient(socket, password);
                }
            }
        }
        client = sharedClient;
        int procid = Integer.parseInt(
            getProperties().getProperty("procid", "0"));
        idPrefix = (long) procid << ID_BITS;
    }

    @Override
    public void cleanup() {
        drainPending();
    }

    private void drainPending() {
        if (pendingId != -1) {
            client.release(pendingId);
            pendingId = -1;
        }
    }

    private long keyToId(String key) {
        int start = (key.length() > 4
                     && key.charAt(0) == 'u' && key.charAt(1) == 's'
                     && key.charAt(2) == 'e' && key.charAt(3) == 'r') ? 4 : 0;
        long n = 0;
        for (int i = start; i < key.length(); i++) {
            n = n * 10 + (key.charAt(i) - '0');
        }
        return idPrefix | (n & ID_MASK);
    }

    @Override
    public Status read(String table, String key, Set<String> fields,
                       Map<String, ByteIterator> result) {
        drainPending();
        long id = keyToId(key);
        ByteBuffer payload = client.get(id);
        if (payload == null || payload.capacity() == 0) {
            return Status.NOT_FOUND;
        }
        decodeMultiPut(payload, fields, result);
        pendingId = id;
        return Status.OK;
    }

    @Override
    public Status scan(String table, String startKey, int recordCount,
                       Set<String> fields,
                       Vector<HashMap<String, ByteIterator>> result) {
        return Status.NOT_IMPLEMENTED;
    }

    @Override
    public Status insert(String table, String key,
                         Map<String, ByteIterator> values) {
        drainPending();
        materialize(values);
        client.multiput(keyToId(key), fieldsBuf, valsBuf);
        return Status.OK;
    }

    @Override
    public Status update(String table, String key,
                         Map<String, ByteIterator> values) {
        drainPending();
        materialize(values);
        client.multiupdate(keyToId(key), fieldsBuf, valsBuf);
        return Status.OK;
    }

    @Override
    public Status delete(String table, String key) {
        drainPending();
        client.delete(keyToId(key));
        return Status.OK;
    }

    private void materialize(Map<String, ByteIterator> values) {
        int n = values.size();
        if (fieldsBuf == null || fieldsBuf.length != n) {
            fieldsBuf = new String[n];
            valsBuf = new Object[n];
            valBytesBuf = new byte[n][];
        }
        int i = 0;
        for (Map.Entry<String, ByteIterator> e : values.entrySet()) {
            fieldsBuf[i] = e.getKey();
            ByteIterator it = e.getValue();
            int len = (int) it.bytesLeft();
            byte[] buf = valBytesBuf[i];
            if (buf == null || buf.length != len) {
                buf = new byte[len];
                valBytesBuf[i] = buf;
            }
            int k = 0;
            while (it.hasNext()) {
                buf[k++] = it.nextByte();
            }
            valsBuf[i] = buf;
            i++;
        }
    }

    private static void decodeMultiPut(ByteBuffer payload,
                                       Set<String> wanted,
                                       Map<String, ByteIterator> out) {
        payload.order(ByteOrder.nativeOrder());
        int n = (int) payload.getLong(0);
        int header = 8 + 16 * n;
        int namePos = header;
        String[] names = new String[n];
        long[] valueEnds = new long[n];
        for (int i = 0; i < n; i++) {
            long nameEnd = payload.getLong(8 + 16 * i);
            valueEnds[i] = payload.getLong(8 + 16 * i + 8);
            int nameLen = (int) (nameEnd - namePos);
            byte[] nb = new byte[nameLen];
            payload.position(namePos);
            payload.get(nb);
            names[i] = new String(nb, StandardCharsets.UTF_8);
            namePos = (int) nameEnd;
        }
        int valuePos = namePos;
        for (int i = 0; i < n; i++) {
            int vlen = (int) (valueEnds[i] - valuePos);
            if (wanted == null || wanted.contains(names[i])) {
                payload.position(valuePos);
                ByteBuffer view = payload.slice();
                view.limit(vlen);
                out.put(names[i],
                    new RecordCodec.ByteBufferByteIterator(view, vlen));
            }
            valuePos = (int) valueEnds[i];
        }
    }
}
