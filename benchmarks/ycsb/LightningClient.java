package site.ycsb.db;

import jlightning.JlightningClient;
import site.ycsb.ByteIterator;
import site.ycsb.DB;
import site.ycsb.DBException;
import site.ycsb.Status;

import java.nio.ByteBuffer;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.Vector;

public class LightningClient extends DB {

    private JlightningClient client;
    private long pendingId = -1;

    @Override
    public void init() throws DBException {
        String socket = getProperties().getProperty(
            "lightning.socket", "/tmp/lightning");
        String password = getProperties().getProperty(
            "lightning.password", "password");
        client = new JlightningClient(socket, password);
    }

    @Override
    public void cleanup() {
        drainPending();
    }

    private static long keyToId(String key) {
        String numStr = key.startsWith("user") ? key.substring(4) : key;
        return Long.parseUnsignedLong(numStr);
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
        pendingId = id;
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
    public Status insert(String table, String key,
                         Map<String, ByteIterator> values) {
        drainPending();
        long id = keyToId(key);
        RecordCodec.Encoded enc = RecordCodec.Encoded.of(values);
        ByteBuffer dst = client.create(id, enc.totalBytes);
        enc.writeTo(dst);
        client.seal(id);
        return Status.OK;
    }

    @Override
    public Status update(String table, String key,
                         Map<String, ByteIterator> values) {
        drainPending();
        long id = keyToId(key);
        client.delete(id);
        RecordCodec.Encoded enc = RecordCodec.Encoded.of(values);
        ByteBuffer dst = client.create(id, enc.totalBytes);
        enc.writeTo(dst);
        client.seal(id);
        return Status.OK;
    }

    @Override
    public Status delete(String table, String key) {
        drainPending();
        client.delete(keyToId(key));
        return Status.OK;
    }

    private void drainPending() {
        if (pendingId >= 0) {
            client.release(pendingId);
            pendingId = -1;
        }
    }
}
