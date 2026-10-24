package sharepack.server;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;

/**
 * Launcher code: address + pack port + key, encoded as Crockford base32.
 * Bytes: type (1 = IPv4, 2 = domain), address (4 bytes, or length + string), port (2), key (4), checksum (1).
 * An IPv4 code is 20 characters: XXXX-XXXX-XXXX-XXXX-XXXX.
 */
final class Code {
    static final int KEY_BYTES = 4;
    private static final String ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

    static String encode(String host, int port, byte[] key) {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        String[] ip = host.split("\\.");
        if (host.matches("\\d{1,3}(\\.\\d{1,3}){3}")) {
            b.write(1);
            for (String part : ip) b.write(Integer.parseInt(part));
        } else {
            byte[] h = host.getBytes(StandardCharsets.US_ASCII);
            b.write(2);
            b.write(h.length);
            b.write(h, 0, h.length);
        }
        b.write(port >> 8);
        b.write(port);
        b.write(key, 0, key.length);
        int sum = 0;
        for (byte x : b.toByteArray()) sum = (sum * 31 + (x & 0xff)) & 0xff;
        b.write(sum);
        return group(base32(b.toByteArray()));
    }

    private static String base32(byte[] data) {
        StringBuilder sb = new StringBuilder();
        int buffer = 0, bits = 0;
        for (byte x : data) {
            buffer = (buffer << 8) | (x & 0xff);
            bits += 8;
            while (bits >= 5) {
                sb.append(ALPHABET.charAt((buffer >> (bits - 5)) & 31));
                bits -= 5;
            }
        }
        if (bits > 0) sb.append(ALPHABET.charAt((buffer << (5 - bits)) & 31));
        return sb.toString();
    }

    private static String group(String s) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            if (i > 0 && i % 4 == 0) sb.append('-');
            sb.append(s.charAt(i));
        }
        return sb.toString();
    }
}
