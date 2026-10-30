package sharepack.server;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Port forwarding on a home router through UPnP (Internet Gateway Device). */
final class Upnp {
    private static final String[] SEARCH = {
            "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
            "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
            "urn:schemas-upnp-org:service:WANIPConnection:1",
    };
    /** IGD 2 routers may refuse permanent forwards, so a lease is used and renewed. */
    static final int LEASE_SECONDS = 3600;

    final String controlUrl;
    final String serviceType;
    /** This computer's address in the home network, the target of the forward. */
    final String localIp;

    private Upnp(String controlUrl, String serviceType, String localIp) {
        this.controlUrl = controlUrl;
        this.serviceType = serviceType;
        this.localIp = localIp;
    }

    /** A home network: an adapter with a private IPv4 and none with a public one (a VPS or a dedicated server). */
    static boolean behindRouter() {
        boolean home = false;
        try {
            for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback()) continue;
                for (InetAddress a : Collections.list(ni.getInetAddresses())) {
                    if (!(a instanceof Inet4Address) || a.isLinkLocalAddress()) continue;
                    if (a.isSiteLocalAddress()) home = true;
                    else if (SharepackServer.isPublicIPv4(a.getHostAddress())) return false;
                }
            }
        } catch (IOException ignored) {
        }
        return home;
    }

    /**
     * The router, or null when nothing answered within timeoutMs (UPnP off, or no home router at all).
     * The search goes out of every home-network adapter: with a VPN on, the default route leads into the VPN.
     */
    static Upnp find(int timeoutMs) {
        List<DatagramSocket> sockets = new ArrayList<>();
        try {
            for (InetAddress a : homeAddresses()) {
                try {
                    DatagramSocket s = new DatagramSocket(new InetSocketAddress(a, 0));
                    s.setSoTimeout(100);
                    sockets.add(s);
                    InetSocketAddress group = new InetSocketAddress("239.255.255.250", 1900);
                    for (String st : SEARCH) {
                        byte[] msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
                                + "MX: 2\r\nST: " + st + "\r\n\r\n").getBytes(StandardCharsets.US_ASCII);
                        s.send(new DatagramPacket(msg, msg.length, group));
                    }
                } catch (IOException ignored) {
                }
            }
            long deadline = System.currentTimeMillis() + timeoutMs;
            byte[] buf = new byte[2048];
            while (!sockets.isEmpty() && System.currentTimeMillis() < deadline) {
                for (DatagramSocket s : sockets) {
                    DatagramPacket p = new DatagramPacket(buf, buf.length);
                    try {
                        s.receive(p);
                    } catch (IOException e) {
                        continue;
                    }
                    Matcher m = Pattern.compile("(?im)^location:\\s*(\\S+)")
                            .matcher(new String(p.getData(), 0, p.getLength(), StandardCharsets.US_ASCII));
                    if (!m.find()) continue;
                    Upnp u = fromDescription(m.group(1));
                    if (u != null) return u;
                }
            }
        } finally {
            for (DatagramSocket s : sockets) s.close();
        }
        return null;
    }

    /** IPv4 addresses of this computer in private ranges (home networks). */
    private static List<InetAddress> homeAddresses() {
        List<InetAddress> out = new ArrayList<>();
        try {
            for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback()) continue;
                for (InetAddress a : Collections.list(ni.getInetAddresses())) {
                    if (a instanceof Inet4Address && a.isSiteLocalAddress()) out.add(a);
                }
            }
        } catch (IOException ignored) {
        }
        return out;
    }

    private static Upnp fromDescription(String location) {
        try {
            URL url = new URL(location);
            String xml = new String(get(url), StandardCharsets.UTF_8);
            for (String svc : new String[]{"WANIPConnection", "WANPPPConnection"}) {
                Matcher m = Pattern.compile("<serviceType>(urn:schemas-upnp-org:service:" + svc
                        + ":\\d)</serviceType>.*?<controlURL>([^<]+)</controlURL>", Pattern.DOTALL).matcher(xml);
                if (!m.find()) continue;
                String local;
                try (DatagramSocket s = new DatagramSocket()) {
                    s.connect(InetAddress.getByName(url.getHost()), 1900);
                    local = s.getLocalAddress().getHostAddress();
                }
                return new Upnp(new URL(url, m.group(2).trim()).toString(), m.group(1), local);
            }
        } catch (IOException ignored) {
        }
        return null;
    }

    /** The router's own internet address, or null. */
    String externalIp() {
        try {
            Matcher m = Pattern.compile("<NewExternalIPAddress>([^<]*)<").matcher(call("GetExternalIPAddress", ""));
            return m.find() ? m.group(1).trim() : null;
        } catch (IOException e) {
            return null;
        }
    }

    /** Forwards TCP port to this computer. Returns the lease in seconds (0 = permanent). */
    int forward(int port, String description) throws IOException {
        try {
            add(port, description, LEASE_SECONDS);
            return LEASE_SECONDS;
        } catch (IOException e) {
            // 725 OnlyPermanentLeasesSupported
            if (!String.valueOf(e.getMessage()).contains("725")) throw e;
            add(port, description, 0);
            return 0;
        }
    }

    /** Whether the router has a TCP forward for this port, and to which address; null when there is none. */
    String forwardedTo(int port) {
        try {
            Matcher m = Pattern.compile("<NewInternalClient>([^<]*)<").matcher(call("GetSpecificPortMappingEntry",
                    "<NewRemoteHost></NewRemoteHost><NewExternalPort>" + port + "</NewExternalPort><NewProtocol>TCP</NewProtocol>"));
            return m.find() ? m.group(1) : "?";
        } catch (IOException e) {
            return null;
        }
    }

    void remove(int port) {
        try {
            call("DeletePortMapping", "<NewRemoteHost></NewRemoteHost><NewExternalPort>" + port
                    + "</NewExternalPort><NewProtocol>TCP</NewProtocol>");
        } catch (IOException ignored) {
        }
    }

    private void add(int port, String description, int lease) throws IOException {
        call("AddPortMapping", "<NewRemoteHost></NewRemoteHost><NewExternalPort>" + port + "</NewExternalPort>"
                + "<NewProtocol>TCP</NewProtocol><NewInternalPort>" + port + "</NewInternalPort>"
                + "<NewInternalClient>" + localIp + "</NewInternalClient><NewEnabled>1</NewEnabled>"
                + "<NewPortMappingDescription>" + description + "</NewPortMappingDescription>"
                + "<NewLeaseDuration>" + lease + "</NewLeaseDuration>");
    }

    /** SOAP call; a router error becomes an IOException with the UPnP error code and text. */
    private String call(String action, String args) throws IOException {
        String body = "<?xml version=\"1.0\"?><s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\""
                + " s:encodingStyle=\"http://schemas.xmlsoap.org/soap/encoding/\"><s:Body><u:" + action
                + " xmlns:u=\"" + serviceType + "\">" + args + "</u:" + action + "></s:Body></s:Envelope>";
        HttpURLConnection c = (HttpURLConnection) new URL(controlUrl).openConnection();
        c.setConnectTimeout(3000);
        c.setReadTimeout(5000);
        c.setDoOutput(true);
        c.setRequestMethod("POST");
        c.setRequestProperty("Content-Type", "text/xml; charset=\"utf-8\"");
        c.setRequestProperty("SOAPAction", "\"" + serviceType + "#" + action + "\"");
        try (OutputStream out = c.getOutputStream()) {
            out.write(body.getBytes(StandardCharsets.UTF_8));
        }
        if (c.getResponseCode() >= 400) {
            InputStream err = c.getErrorStream();
            String text = err == null ? "" : new String(readAll(err), StandardCharsets.UTF_8);
            Matcher code = Pattern.compile("<errorCode>(\\d+)<").matcher(text);
            Matcher desc = Pattern.compile("<errorDescription>([^<]*)<").matcher(text);
            throw new IOException((code.find() ? code.group(1) : "HTTP " + c.getResponseCode())
                    + (desc.find() ? " " + desc.group(1) : ""));
        }
        return new String(readAll(c.getInputStream()), StandardCharsets.UTF_8);
    }

    private static byte[] get(URL url) throws IOException {
        HttpURLConnection c = (HttpURLConnection) url.openConnection();
        c.setConnectTimeout(3000);
        c.setReadTimeout(5000);
        return readAll(c.getInputStream());
    }

    private static byte[] readAll(InputStream in) throws IOException {
        try (InputStream i = in) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = i.read(buf)) > 0) out.write(buf, 0, n);
            return out.toByteArray();
        }
    }
}
