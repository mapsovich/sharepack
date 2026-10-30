package sharepack.server;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Semaphore;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Serves the modpack to the sharepack launcher. Runs as a mod (Fabric/Quilt/Forge/NeoForge),
 * a plugin (Paper/Spigot) or a standalone program: java -jar sharepack-server.jar
 *
 * Everything lives in the sharepack/ folder next to the server:
 *   config.properties  language, port, address, secret key
 *   pack.json          pack name, game version, loader
 *   files/             synced to players on every launch
 *   defaults/          copied to players once, never overwritten
 */
public final class SharepackServer {
    /** Server part version. It changes only with server changes and must match server/VERSION. */
    static final String VERSION = "v2026.b7";
    static final String RELEASES_API = "https://api.github.com/repos/mapsovich/sharepack/releases/latest";
    /** Version of the server part at a release tag; releases before v2026.b7 have no such file. */
    static final String VERSION_FILE = "https://raw.githubusercontent.com/mapsovich/sharepack/%s/server/VERSION";
    private static final String DIR = "sharepack";
    private static final String OLD_DIR = "packserver";
    /** Connections served at once. The rest are closed, so a flood can't eat the game server's memory. */
    private static final int MAX_CONNECTIONS = 32;
    private static final int MAX_LINE = 8192;
    private static final int MAX_HEADERS = 64;
    private static final long REQUEST_TIMEOUT_MS = 20000;
    /** Wrong keys allowed per IP within FAIL_WINDOW_MS before that IP is ignored until the window ends. */
    private static final int MAX_FAILS = 20;
    private static final long FAIL_WINDOW_MS = 10 * 60 * 1000;

    private static boolean started;
    /** Console and template language, from language= in config.properties. */
    private static boolean ru;

    private final File dir;
    private final GameInfo game;
    private final Map<String, String[]> hashCache = new ConcurrentHashMap<>();
    private final Semaphore slots = new Semaphore(MAX_CONNECTIONS);
    /** ip -> {failures, window start} */
    private final Map<String, long[]> fails = new ConcurrentHashMap<>();
    /** Access key from the URL; signing key for the manifest. Both are derived from the secret in the code. */
    private String accessKey;
    private byte[] signingKey;

    private SharepackServer(File dir, GameInfo game) {
        this.dir = dir;
        this.game = game;
    }

    /** platform: fabric, forge, neoforge, bukkit or standalone. */
    public static synchronized void start(String platform) {
        if (started) return;
        started = true;
        try {
            File old = new File(OLD_DIR).getAbsoluteFile();
            File dir = new File(DIR).getAbsoluteFile();
            if (old.isDirectory() && !dir.exists() && !old.renameTo(dir)) dir = old;
            new SharepackServer(dir, GameInfo.detect(platform)).run();
        } catch (Exception e) {
            log(tr("Не удалось запустить sharepack: ", "Failed to start sharepack: ") + e);
        }
    }

    public static void main(String[] args) throws Exception {
        start("standalone");
        Thread.currentThread().join();
    }

    /** Bukkit replaces this with the plugin logger, everything else prints to stdout. */
    static java.util.function.Consumer<String> logger = s -> System.out.println("[sharepack] " + s);

    static void log(String s) {
        logger.accept(s);
    }

    static String tr(String ruText, String enText) {
        return ru ? ruText : enText;
    }

    /** auto picks Russian for ru/uk/be/kk system locales. */
    private static boolean pickRussian(String lang) {
        lang = lang.trim().toLowerCase(Locale.ROOT);
        if (lang.equals("ru")) return true;
        if (lang.equals("en")) return false;
        return Arrays.asList("ru", "uk", "be", "kk").contains(Locale.getDefault().getLanguage());
    }

    /** The mod is loaded on clients too, where it has nothing to do. fmlPackage: net.minecraftforge or net.neoforged. */
    public static boolean isClient(String fmlPackage) {
        try {
            Object dist = Class.forName(fmlPackage + ".fml.loading.FMLEnvironment").getField("dist").get(null);
            return "CLIENT".equals(String.valueOf(dist));
        } catch (Throwable t) {
            return false;
        }
    }

    private void run() throws IOException {
        new File(dir, "files/mods").mkdirs();
        new File(dir, "defaults").mkdirs();
        Properties cfg = loadConfig();
        File packJson = new File(dir, "pack.json");
        if (!packJson.exists()) {
            writeText(packJson, "{\n"
                    + "  \"name\": \"" + tr("Мой сервер", "My server") + "\",\n"
                    + "  \"version\": \"1.0\",\n"
                    + "  \"minecraft\": \"auto\",\n"
                    + "  \"loader\": \"auto\",\n"
                    + "  \"loader_version\": \"auto\",\n"
                    + "  \"memory\": 4096,\n"
                    + "  \"managed_dirs\": [\"mods\"]\n"
                    + "}\n");
            log(tr("Создан " + packJson + ".", "Created " + packJson + "."));
        }
        if (game.minecraft != null) {
            log(tr("Сервер: ", "Server: ") + game);
        } else {
            log(tr("Версию Minecraft определить не удалось. Укажите minecraft и loader в " + packJson + ".",
                    "Could not detect the Minecraft version. Set minecraft and loader in " + packJson + "."));
        }

        byte[] secret = hexToBytes(cfg.getProperty("key"));
        accessKey = Code.accessKey(secret);
        signingKey = Code.signingKey(secret);
        int httpPort = Integer.parseInt(cfg.getProperty("http-port", "25566").trim());
        String[] address = detectAddress(cfg);

        ServerSocket socket = new ServerSocket(httpPort);
        Thread t = new Thread(() -> acceptLoop(socket), "sharepack");
        t.setDaemon(true);
        t.start();
        checkForUpdate();

        File cfgFile = new File(dir, "config.properties");
        if (address == null) {
            log(tr("Раздача работает на порту " + httpPort + ", но внешний IP не определён.",
                    "Serving on port " + httpPort + ", but the public IP could not be detected."));
            log(tr("Укажите public-address в " + cfgFile + " и перезапустите сервер.",
                    "Set public-address in " + cfgFile + " and restart the server."));
            return;
        }
        String code = Code.encode(address[0], httpPort, secret);
        writeText(new File(dir, "CODE.txt"), code + "\n");
        log(tr("Адрес в коде: " + address[0] + " (источник: " + address[1] + ").",
                "Address in the code: " + address[0] + " (source: " + address[1] + ")."));
        log(tr("Если игроки не могут подключиться, укажите public-address в " + cfgFile + ".",
                "If players cannot connect, set public-address in " + cfgFile + "."));
        if (onPanelHosting()) {
            log(tr("Обнаружена панель хостинга: добавьте в ней ещё один порт (allocation),",
                    "Hosting panel detected: add one more port (allocation) in the panel,"));
            log(tr("укажите его в http-port в " + cfgFile + " и перезапустите сервер.",
                    "set it as http-port in " + cfgFile + " and restart the server."));
        } else if (useUpnp(cfg)) {
            openRouterPort(httpPort, address);
        } else {
            log(tr("Порт раздачи: " + httpPort + " (TCP). Он должен быть открыт на роутере.",
                    "Pack port: " + httpPort + " (TCP). It must be forwarded on your router."));
        }
        log("==============================================");
        log(tr("  Код для лаунчера: ", "  Launcher code: ") + code);
        log("==============================================");
    }

    private Properties loadConfig() throws IOException {
        File f = new File(dir, "config.properties");
        Properties p = new Properties();
        if (f.exists()) {
            try (Reader r = new InputStreamReader(new FileInputStream(f), StandardCharsets.UTF_8)) {
                p.load(r);
            }
        }
        String lang = p.getProperty("language", "auto").trim();
        ru = pickRussian(lang);
        // Rewrite the file when the key is missing, has the old length, or the file predates the language setting.
        if (p.getProperty("key", "").trim().length() != Code.SECRET_BYTES * 2 || p.getProperty("language") == null
                || p.getProperty("upnp") == null) {
            String keyHex = p.getProperty("key", "").trim();
            if (keyHex.length() != Code.SECRET_BYTES * 2) {
                if (!keyHex.isEmpty()) {
                    log(tr("Формат кода изменился, создан новый ключ. Старый код больше не работает.",
                            "The code format has changed and a new key was generated. The old code no longer works."));
                }
                byte[] k = new byte[Code.SECRET_BYTES];
                new SecureRandom().nextBytes(k);
                keyHex = bytesToHex(k);
            }
            writeText(f, tr("# Язык консоли: auto, ru или en. Если русский текст в консоли отображается\n"
                            + "# неправильно, укажите en\n",
                            "# Console language: auto, ru or en\n")
                    + "language=" + lang + "\n"
                    + tr("# Порт, с которого лаунчер скачивает сборку\n", "# Port the launcher downloads the pack from\n")
                    + "http-port=" + p.getProperty("http-port", "25566") + "\n"
                    + tr("# Внешний IP или домен. Если пусто, определяется автоматически\n",
                            "# Public IP or domain. Detected automatically if empty\n")
                    + "public-address=" + p.getProperty("public-address", "") + "\n"
                    + tr("# Открывать порт раздачи на домашнем роутере автоматически (UPnP): auto или off.\n"
                                    + "# На хостинге и на сервере с публичным IP не используется\n",
                            "# Forward the pack port on a home router automatically (UPnP): auto or off.\n"
                                    + "# Not used on hostings and on servers with a public IP\n")
                    + "upnp=" + p.getProperty("upnp", "auto") + "\n"
                    + tr("# Секретный ключ, входит в код. Никому не передавайте его отдельно от кода. Если удалить\n"
                                    + "# строку, будет создан новый ключ и старый код перестанет работать\n",
                            "# Secret key, part of the code. Don't share it apart from the code. Delete this line\n"
                                    + "# to generate a new key; the old code will stop working\n")
                    + "key=" + keyHex + "\n");
            p.setProperty("key", keyHex);
        }
        p.setProperty("key", p.getProperty("key").trim().toLowerCase(Locale.ROOT));
        return p;
    }

    /**
     * {address, source} or null. ipify goes last: on panel hostings the node may have several IPs,
     * and the outgoing one is not always the one the server listens on.
     */
    private static String[] detectAddress(Properties cfg) {
        String manual = cfg.getProperty("public-address", "").trim();
        if (!manual.isEmpty()) return new String[]{manual, "public-address, config.properties"};
        // Pterodactyl/Pelican pass the IP of the primary allocation.
        String panel = System.getenv("SERVER_IP");
        if (isPublicIPv4(panel)) return new String[]{panel.trim(), "SERVER_IP"};
        String props = readServerProperty("server-ip", "");
        if (isPublicIPv4(props)) return new String[]{props.trim(), "server-ip, server.properties"};
        String ip = detectPublicIp();
        return ip == null ? null : new String[]{ip, "api.ipify.org"};
    }

    /**
     * UPnP only makes sense behind a home router: the computer has a private address and no hosting panel.
     * On a VPS or a dedicated server the address is public and there is no router to ask.
     */
    private static boolean useUpnp(Properties cfg) {
        return !"off".equalsIgnoreCase(cfg.getProperty("upnp", "auto").trim()) && Upnp.behindRouter();
    }

    /** Finds the router, checks its internet address and forwards the pack port. Runs in the background. */
    private static void openRouterPort(int port, String[] address) {
        Thread t = new Thread(() -> {
            Upnp router = Upnp.find(3000);
            if (router == null) {
                log(tr("Порт раздачи: " + port + " (TCP). Открыть его на роутере автоматически не удалось:",
                        "Pack port: " + port + " (TCP). It could not be forwarded on the router automatically:"));
                log(tr("UPnP на роутере выключен или не поддерживается. Откройте порт вручную.",
                        "UPnP is off or not supported on the router. Forward the port by hand."));
                return;
            }
            String external = router.externalIp();
            if (external != null && !external.isEmpty() && !isPublicIPv4(external)) {
                log(tr("Провайдер выдал роутеру серый адрес " + external + ": один внешний IP на многих абонентов.",
                        "Your ISP gave the router a shared (CGNAT) address " + external + "."));
                log(tr("Игроки из интернета не смогут подключиться, даже если открыть порт. Закажите у провайдера",
                        "Players from the internet can't connect even with the port forwarded. Ask your ISP for"));
                log(tr("белый IP или используйте общую виртуальную сеть (Radmin VPN, ZeroTier) и укажите её адрес в public-address.",
                        "a public IP, or use a virtual network (Radmin VPN, ZeroTier) and set its address as public-address."));
                return;
            }
            if (external != null && "api.ipify.org".equals(address[1]) && !external.equals(address[0])) {
                log(tr("Адрес роутера в интернете (" + external + ") не совпадает с внешним IP (" + address[0] + "):",
                        "The router's internet address (" + external + ") differs from the public IP (" + address[0] + "):"));
                log(tr("похоже, включён VPN или роутер подключён через ещё один роутер. Выключите VPN",
                        "a VPN seems to be on, or the router is behind another router. Turn the VPN off"));
                log(tr("или откройте порт и на втором роутере, иначе игроки не подключатся.",
                        "or forward the port on the other router too, otherwise players can't connect."));
            }
            int lease;
            try {
                lease = router.forward(port, "sharepack");
            } catch (IOException e) {
                log(tr("Не удалось открыть порт " + port + " на роутере через UPnP (" + e.getMessage() + "). Откройте его вручную.",
                        "Could not forward port " + port + " through UPnP (" + e.getMessage() + "). Forward it by hand."));
                return;
            }
            log(tr("Порт раздачи " + port + " (TCP) открыт на роутере автоматически (UPnP).",
                    "Pack port " + port + " (TCP) was forwarded on the router automatically (UPnP)."));
            Runtime.getRuntime().addShutdownHook(new Thread(() -> router.remove(port)));
            // the forward expires unless renewed
            while (lease > 0) {
                try {
                    Thread.sleep(lease * 500L);
                    lease = router.forward(port, "sharepack");
                } catch (InterruptedException e) {
                    return;
                } catch (IOException e) {
                    log(tr("Не удалось продлить открытие порта " + port + " на роутере: " + e.getMessage(),
                            "Could not renew the forward of port " + port + " on the router: " + e.getMessage()));
                    return;
                }
            }
        }, "sharepack-upnp");
        t.setDaemon(true);
        t.start();
    }

    /** Pterodactyl/Pelican: a second port can only be added in the panel. */
    private static boolean onPanelHosting() {
        return System.getenv("P_SERVER_UUID") != null || System.getenv("P_SERVER_LOCATION") != null;
    }

    /** Not empty, not 0.0.0.0, not a private range and not carrier-grade NAT. */
    static boolean isPublicIPv4(String s) {
        if (s == null || !s.trim().matches("\\d{1,3}(\\.\\d{1,3}){3}")) return false;
        String[] p = s.trim().split("\\.");
        int a = Integer.parseInt(p[0]), b = Integer.parseInt(p[1]);
        for (String part : p) if (Integer.parseInt(part) > 255) return false;
        return !(a == 0 || a == 10 || a == 127 || a >= 224
                || (a == 169 && b == 254) || (a == 172 && b >= 16 && b <= 31)
                || (a == 192 && b == 168) || (a == 100 && b >= 64 && b <= 127));
    }

    private static String detectPublicIp() {
        try {
            String ip = new String(httpGet("https://api.ipify.org"), StandardCharsets.US_ASCII).trim();
            return ip.matches("\\d{1,3}(\\.\\d{1,3}){3}") ? ip : null;
        } catch (IOException e) {
            return null;
        }
    }

    // ---------------- Updates ----------------

    private static void checkForUpdate() {
        Thread t = new Thread(() -> {
            try {
                String json = new String(httpGet(RELEASES_API), StandardCharsets.UTF_8);
                String tag = jsonField(json, "tag_name");
                String url = jsonField(json, "html_url");
                if (tag == null || !isNewer(tag, VERSION)) return;
                // the release may change only the launcher
                String version = new String(httpGet(String.format(VERSION_FILE, tag)), StandardCharsets.UTF_8).trim();
                if (isNewer(version, VERSION)) {
                    log(tr("Доступна новая версия sharepack: " + version + " (установлена " + VERSION + "). " + url,
                            "A new sharepack version is available: " + version + " (installed " + VERSION + "). " + url));
                }
            } catch (IOException ignored) {
            }
        }, "sharepack-update");
        t.setDaemon(true);
        t.start();
    }

    /** Compares the numbers in the tags: v2026.b2 > v2026.b1. */
    static boolean isNewer(String tag, String current) {
        List<Integer> a = numbers(tag), b = numbers(current);
        for (int i = 0; i < Math.max(a.size(), b.size()); i++) {
            int x = i < a.size() ? a.get(i) : 0, y = i < b.size() ? b.get(i) : 0;
            if (x != y) return x > y;
        }
        return false;
    }

    private static List<Integer> numbers(String s) {
        List<Integer> out = new ArrayList<>();
        Matcher m = Pattern.compile("\\d+").matcher(s);
        while (m.find()) out.add(Integer.parseInt(m.group()));
        return out;
    }

    // ---------------- HTTP ----------------

    private void acceptLoop(ServerSocket socket) {
        while (true) {
            Socket s;
            try {
                s = socket.accept();
            } catch (IOException e) {
                log(tr("Ошибка приёма соединения: ", "Accept failed: ") + e);
                continue;
            }
            if (blocked(s.getInetAddress().getHostAddress()) || !slots.tryAcquire()) {
                closeQuietly(s);
                continue;
            }
            Thread t = new Thread(() -> {
                try {
                    handle(s);
                } finally {
                    slots.release();
                }
            }, "sharepack-conn");
            t.setDaemon(true);
            t.start();
        }
    }

    private void handle(Socket s) {
        try (Socket sock = s) {
            sock.setSoTimeout(10000);
            long deadline = System.currentTimeMillis() + REQUEST_TIMEOUT_MS;
            InputStream in = new BufferedInputStream(sock.getInputStream());
            String requestLine = readLine(in, deadline);
            if (requestLine == null) return;
            String line;
            int headers = 0;
            while ((line = readLine(in, deadline)) != null && !line.isEmpty()) {
                if (++headers > MAX_HEADERS) return;
            }
            OutputStream out = new BufferedOutputStream(sock.getOutputStream());
            String[] parts = requestLine.split(" ");
            if (parts.length < 2 || !(parts[0].equals("GET") || parts[0].equals("HEAD"))) {
                sendError(out, 405);
                return;
            }
            serve(parts[0].equals("HEAD"), parts[1], sock.getInetAddress().getHostAddress(), out);
            out.flush();
        } catch (IOException | IllegalArgumentException ignored) {
        }
    }

    /** A line of at most MAX_LINE bytes; the whole request must arrive before the deadline. */
    private static String readLine(InputStream in, long deadline) throws IOException {
        ByteArrayOutputStream line = new ByteArrayOutputStream();
        int c;
        while ((c = in.read()) != -1) {
            if (c == '\n') break;
            if (line.size() >= MAX_LINE || System.currentTimeMillis() > deadline) throw new IOException("request too long");
            if (c != '\r') line.write(c);
        }
        if (c == -1 && line.size() == 0) return null;
        return new String(line.toByteArray(), StandardCharsets.ISO_8859_1);
    }

    private boolean blocked(String ip) {
        long[] f = fails.get(ip);
        return f != null && f[0] >= MAX_FAILS && System.currentTimeMillis() - f[1] < FAIL_WINDOW_MS;
    }

    /** Counts a wrong key. Guessing the key means millions of tries, so a few dozen per IP is plenty. */
    private void recordFail(String ip) {
        long now = System.currentTimeMillis();
        if (fails.size() > 10000) fails.clear();
        long[] f = fails.compute(ip, (k, v) -> v == null || now - v[1] >= FAIL_WINDOW_MS ? new long[]{0, now} : v);
        synchronized (f) {
            if (++f[0] == MAX_FAILS) {
                log(tr("Слишком много запросов с неверным кодом с " + ip + ", адрес заблокирован на 10 минут.",
                        "Too many requests with a wrong code from " + ip + ", blocked for 10 minutes."));
            }
        }
    }

    private static void closeQuietly(Socket s) {
        try {
            s.close();
        } catch (IOException ignored) {
        }
    }

    private void serve(boolean head, String rawPath, String ip, OutputStream out) throws IOException {
        String path = URLDecoder.decode(rawPath.split("\\?")[0].replace("+", "%2B"), "UTF-8");
        String[] seg = path.replaceFirst("^/+", "").split("/", 3);
        // Wrong key: answer 404 as if nothing is there.
        if (seg.length < 2 || !MessageDigest.isEqual(seg[0].toLowerCase(Locale.ROOT).getBytes(StandardCharsets.US_ASCII),
                accessKey.getBytes(StandardCharsets.US_ASCII))) {
            recordFail(ip);
            sendError(out, 404);
            return;
        }
        if (seg.length == 2 && seg[1].equals("manifest.json")) {
            byte[] body;
            try {
                body = buildManifest().getBytes(StandardCharsets.UTF_8);
            } catch (IOException e) {
                log(tr("Не удалось собрать список файлов: ", "Failed to build the file list: ") + e);
                sendError(out, 500);
                return;
            }
            // The launcher checks this signature, so a manifest changed on the way is rejected.
            out.write(("HTTP/1.1 200 OK\r\n"
                    + "Content-Type: application/json; charset=utf-8\r\n"
                    + "Content-Length: " + body.length + "\r\n"
                    + "X-Sharepack-Signature: " + bytesToHex(hmac(signingKey, body)) + "\r\n"
                    + "Connection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
            if (!head) out.write(body);
            return;
        }
        if (seg.length == 3 && (seg[1].equals("files") || seg[1].equals("defaults"))) {
            File base = new File(dir, seg[1]).getCanonicalFile();
            File f = new File(base, seg[2]).getCanonicalFile();
            if (f.isFile() && f.getPath().startsWith(base.getPath() + File.separator)) {
                sendHeaders(out, 200, "application/octet-stream", f.length());
                if (!head) {
                    try (InputStream fin = new FileInputStream(f)) {
                        copy(fin, out);
                    }
                }
                return;
            }
        }
        sendError(out, 404);
    }

    private static void sendHeaders(OutputStream out, int status, String type, long length) throws IOException {
        String reason = status == 200 ? "OK" : status == 404 ? "Not Found" : status == 405 ? "Method Not Allowed" : "Error";
        out.write(("HTTP/1.1 " + status + " " + reason + "\r\n"
                + "Content-Type: " + type + "\r\n"
                + "Content-Length: " + length + "\r\n"
                + "Connection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
    }

    private static void sendError(OutputStream out, int status) throws IOException {
        sendHeaders(out, status, "text/plain", 0);
        out.flush();
    }

    // ---------------- Manifest ----------------

    private String buildManifest() throws IOException {
        String pack = new String(readAll(new FileInputStream(new File(dir, "pack.json"))), StandardCharsets.UTF_8).trim();
        if (pack.startsWith("﻿")) pack = pack.substring(1);
        pack = fillAuto(pack);
        StringBuilder sb = new StringBuilder();
        sb.append("{\"pack\":").append(pack);
        sb.append(",\"server_port\":").append(readServerPort());
        sb.append(",\"files\":[");
        Set<String> seen = new HashSet<>();
        boolean first = true;
        for (String src : new String[]{"files", "defaults"}) {
            File base = new File(dir, src);
            List<File> all = new ArrayList<>();
            collect(base, all);
            for (File f : all) {
                String rel = base.toURI().relativize(f.toURI()).getPath();
                if (!seen.add(rel)) continue;
                String[] h = sha256(f);
                if (!first) sb.append(',');
                first = false;
                sb.append("{\"path\":").append(jsonString(rel))
                        .append(",\"sha256\":\"").append(h[0])
                        .append("\",\"size\":").append(f.length())
                        .append(",\"src\":\"").append(src)
                        .append("\",\"default\":").append(src.equals("defaults"))
                        .append('}');
            }
        }
        return sb.append("]}").toString();
    }

    /** Replaces "auto" (or a missing field) in pack.json with what the server is running. */
    private String fillAuto(String pack) {
        pack = fillField(pack, "minecraft", game.minecraft);
        pack = fillField(pack, "loader", game.loader);
        String loader = jsonField(pack, "loader");
        // A loader version only makes sense for the loader it was detected for; "" means the latest one.
        boolean same = loader != null && loader.trim().equalsIgnoreCase(String.valueOf(game.loader));
        return fillField(pack, "loader_version", same && game.loaderVersion != null ? game.loaderVersion : "");
    }

    private static String fillField(String json, String field, String value) {
        if (value == null) return json;
        Matcher m = Pattern.compile("(\"" + field + "\"\\s*:\\s*)\"\\s*auto\\s*\"", Pattern.CASE_INSENSITIVE).matcher(json);
        if (m.find()) return json.substring(0, m.start()) + m.group(1) + jsonString(value) + json.substring(m.end());
        if (jsonField(json, field) == null && !json.contains("\"" + field + "\"")) {
            int brace = json.indexOf('{');
            if (brace >= 0) return json.substring(0, brace + 1) + jsonString(field) + ":" + jsonString(value) + ","
                    + json.substring(brace + 1);
        }
        return json;
    }

    /** Game port, so the launcher can add the server to the in-game list. */
    private static int readServerPort() {
        try {
            return Integer.parseInt(readServerProperty("server-port", "25565").trim());
        } catch (NumberFormatException e) {
            return 25565;
        }
    }

    private static String readServerProperty(String name, String def) {
        Properties p = new Properties();
        try (InputStream in = new FileInputStream("server.properties")) {
            p.load(in);
            return p.getProperty(name, def);
        } catch (IOException e) {
            return def;
        }
    }

    private static void collect(File d, List<File> out) {
        File[] list = d.listFiles();
        if (list == null) return;
        Arrays.sort(list);
        for (File f : list) {
            if (f.isDirectory()) collect(f, out);
            else out.add(f);
        }
    }

    private String[] sha256(File f) throws IOException {
        String cacheKey = f.getPath() + "|" + f.length() + "|" + f.lastModified();
        String[] cached = hashCache.get(cacheKey);
        if (cached != null) return cached;
        try (InputStream in = new FileInputStream(f)) {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] buf = new byte[1 << 16];
            int n;
            while ((n = in.read(buf)) > 0) md.update(buf, 0, n);
            String[] h = {bytesToHex(md.digest())};
            hashCache.put(cacheKey, h);
            return h;
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new IOException(e);
        }
    }

    // ---------------- Helpers ----------------

    static byte[] hmac(byte[] key, byte[] data) {
        try {
            javax.crypto.Mac mac = javax.crypto.Mac.getInstance("HmacSHA256");
            mac.init(new javax.crypto.spec.SecretKeySpec(key, "HmacSHA256"));
            return mac.doFinal(data);
        } catch (java.security.GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }

    static byte[] httpGet(String url) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(5000);
        c.setReadTimeout(5000);
        c.setRequestProperty("User-Agent", "sharepack-server/" + VERSION);
        return readAll(c.getInputStream());
    }

    /** Value of a top-level string field. Enough for pack.json and GitHub responses. */
    static String jsonField(String json, String field) {
        Matcher m = Pattern.compile("\"" + Pattern.quote(field) + "\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\.)*)\"").matcher(json);
        return m.find() ? m.group(1).replace("\\/", "/").replace("\\\"", "\"").replace("\\\\", "\\") : null;
    }

    static String jsonString(String s) {
        StringBuilder sb = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            if (c == '"' || c == '\\') sb.append('\\').append(c);
            else if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
            else sb.append(c);
        }
        return sb.append('"').toString();
    }

    private static void writeText(File f, String text) throws IOException {
        f.getParentFile().mkdirs();
        try (Writer w = new OutputStreamWriter(new FileOutputStream(f), StandardCharsets.UTF_8)) {
            w.write(text);
        }
    }

    static byte[] readAll(InputStream in) throws IOException {
        try (InputStream i = in) {
            ByteArrayOutputStream bo = new ByteArrayOutputStream();
            copy(i, bo);
            return bo.toByteArray();
        }
    }

    private static void copy(InputStream in, OutputStream out) throws IOException {
        byte[] buf = new byte[1 << 16];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
    }

    static String bytesToHex(byte[] b) {
        StringBuilder sb = new StringBuilder();
        for (byte x : b) sb.append(String.format("%02x", x & 0xff));
        return sb.toString();
    }

    static byte[] hexToBytes(String s) {
        byte[] b = new byte[s.length() / 2];
        for (int i = 0; i < b.length; i++) b[i] = (byte) Integer.parseInt(s.substring(i * 2, i * 2 + 2), 16);
        return b;
    }
}
