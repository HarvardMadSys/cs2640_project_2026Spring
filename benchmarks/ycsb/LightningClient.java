package site.ycsb.db;

import jlightning.JlightningClient;
import site.ycsb.ByteArrayByteIterator;
import site.ycsb.ByteIterator;
import site.ycsb.DB;
import site.ycsb.DBException;
import site.ycsb.Status;

import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.Vector;

public class LightningClient extends DB {

    private JlightningClient client;
    private String[] defaultFields;

    @Override
    public void init() throws DBException {
        int fieldCount = Integer.parseInt(
            getProperties().getProperty("fieldcount", "10"));
        String prefix = getProperties().getProperty("fieldnameprefix", "field");
        defaultFields = new String[fieldCount];
        for (int i = 0; i < fieldCount; i++) {
            defaultFields[i] = prefix + i;
        }
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
        String[] query = fields == null
            ? defaultFields
            : fields.toArray(new String[0]);
        long[] ret = client.multiget(id, query);
        if (ret == null || ret.length == 0) {
            return Status.NOT_FOUND;
        }
        int n = ret.length / 4;
        for (int i = 0; i < n; i++) {
            long fSize = ret[i * 4];
            long fAddr = ret[i * 4 + 1];
            long vSize = ret[i * 4 + 2];
            long vAddr = ret[i * 4 + 3];
            byte[] fName = new byte[(int) fSize];
            client.getbytes(fName, 0, fAddr, fSize);
            byte[] vBytes = new byte[(int) vSize];
            client.getbytes(vBytes, 0, vAddr, vSize);
            result.put(new String(fName, StandardCharsets.UTF_8),
                       new ByteArrayByteIterator(vBytes));
        }
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
    public Status update(String table, String key,
                         Map<String, ByteIterator> values) {
        long id = keyToId(key);
        String[] fields = new String[values.size()];
        byte[][] vals = new byte[values.size()][];
        int i = 0;
        for (Map.Entry<String, ByteIterator> e : values.entrySet()) {
            fields[i] = e.getKey();
            vals[i] = e.getValue().toArray();
            i++;
        }
        client.multiupdate(id, fields, vals);
        return Status.OK;
    }

    @Override
    public Status insert(String table, String key,
                         Map<String, ByteIterator> values) {
        long id = keyToId(key);
        String[] fields = new String[values.size()];
        byte[][] vals = new byte[values.size()][];
        int i = 0;
        for (Map.Entry<String, ByteIterator> e : values.entrySet()) {
            fields[i] = e.getKey();
            vals[i] = e.getValue().toArray();
            i++;
        }
        client.multiput(id, fields, vals);
        return Status.OK;
    }

    @Override
    public Status delete(String table, String key) {
        client.delete(keyToId(key));
        return Status.OK;
    }
}
