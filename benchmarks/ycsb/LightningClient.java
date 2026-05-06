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

    private JlightningClient client;

    @Override
    public void init() throws DBException {
        String socket = getProperties().getProperty(
            "lightning.socket", "/tmp/lightning");
        String password = getProperties().getProperty(
            "lightning.password", "password");
        client = new JlightningClient(socket, password);
    }

    @Override
    public void cleanup() {}

    private static long keyToId(String key) {
        String numStr = key.startsWith("user") ? key.substring(4) : key;
        return Long.parseUnsignedLong(numStr);
    }

    @Override
    public Status read(String table, String key, Set<String> fields,
                       Map<String, ByteIterator> result) {
        long id = keyToId(key);
        ByteBuffer payload = client.get(id);
        if (payload == null || payload.capacity() == 0) {
            return Status.NOT_FOUND;
        }
        decodeMultiPut(payload, fields, result);
        client.release(id);
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
        String[] fields = new String[values.size()];
        Object[] vals = new Object[values.size()];
        materialize(values, fields, vals);
        client.multiput(keyToId(key), fields, vals);
        return Status.OK;
    }

    @Override
    public Status update(String table, String key,
                         Map<String, ByteIterator> values) {
        String[] fields = new String[values.size()];
        Object[] vals = new Object[values.size()];
        materialize(values, fields, vals);
        client.multiupdate(keyToId(key), fields, vals);
        return Status.OK;
    }

    @Override
    public Status delete(String table, String key) {
        client.delete(keyToId(key));
        return Status.OK;
    }

    private static void materialize(Map<String, ByteIterator> values,
                                    String[] fields, Object[] vals) {
        int i = 0;
        for (Map.Entry<String, ByteIterator> e : values.entrySet()) {
            fields[i] = e.getKey();
            vals[i] = e.getValue().toArray();
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
