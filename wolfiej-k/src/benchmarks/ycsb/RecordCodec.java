package site.ycsb.db;

import site.ycsb.ByteIterator;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.Set;

final class RecordCodec {

    private RecordCodec() {}

    static final class Encoded {
        final byte[][] keys;
        final byte[][] vals;
        final int totalBytes;

        private Encoded(byte[][] keys, byte[][] vals, int totalBytes) {
            this.keys = keys;
            this.vals = vals;
            this.totalBytes = totalBytes;
        }

        static Encoded of(Map<String, ByteIterator> values) {
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
            return new Encoded(keys, vals, total);
        }

        void writeTo(ByteBuffer buf) {
            buf.order(ByteOrder.LITTLE_ENDIAN);
            buf.putInt(keys.length);
            for (int j = 0; j < keys.length; j++) {
                buf.putInt(keys[j].length).put(keys[j])
                   .putInt(vals[j].length).put(vals[j]);
            }
        }

        byte[] toByteArray() {
            ByteBuffer buf = ByteBuffer.allocate(totalBytes);
            writeTo(buf);
            return buf.array();
        }
    }

    static void decode(ByteBuffer payload, Set<String> wanted,
                       Map<String, ByteIterator> out) {
        payload.order(ByteOrder.LITTLE_ENDIAN);
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

    static final class ByteBufferByteIterator extends ByteIterator {
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
