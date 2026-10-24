package sharepack.server;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;

/**
 * Launcher code: address + pack port + secret, encoded as Crockford base32.
 * Bytes: type (1 = IPv4, 2 = domain), address (4 bytes, or length + string), port (2), secret (12), checksum (1).
 * An IPv4 code is 32 characters: XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX.
 *
 * The secret itself never goes over the network. Two keys are derived from it:
 * the access key, sent in request URLs, and the signing key for the manifest HMAC. Someone who sees the traffic
 * learns only the access key and can't forge a manifest, so the launcher notices modified files.
 */
final class Code {
    static final int SECRET_BYTES = 12;
    private static final String ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

    static String encode(String host, int port, byte[] secret) {
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
        b.write(secret, 0, secret.length);
        int sum = 0;
        for (byte x : b.toByteArray()) sum = (sum * 31 + (x & 0xff)) & 0xff;
        b.write(sum);
        return group(base32(b.toByteArray()));
    }

    /** 16 hex characters used in URLs and as the profile folder name on the player's side. */
    static String accessKey(byte[] secret) {
        byte[] h = derive("sharepack/access", secret);
        return SharepackServer.bytesToHex(java.util.Arrays.copyOf(h, 8));
    }

    static byte[] signingKey(byte[] secret) {
        return derive("sharepack/sign", secret);
    }

    private static byte[] derive(String label, byte[] secret) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            md.update(label.getBytes(StandardCharsets.US_ASCII));
            md.update(secret);
            return md.digest();
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
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
