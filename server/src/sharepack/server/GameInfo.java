package sharepack.server;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Optional;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/**
 * Minecraft version and mod loader of the running server. Loader APIs are called through reflection,
 * so the jar has no compile-time dependency on any of them. Fields stay null when unknown.
 */
final class GameInfo {
    String minecraft;
    String loader;
    String loaderVersion;

    static GameInfo detect(String platform) {
        GameInfo g = new GameInfo();
        try {
            switch (platform) {
                case "fabric": g.fromFabric(); break;
                case "forge": g.fromForge(); break;
                case "neoforge": g.fromNeoForge(); break;
                case "bukkit": g.fromBukkit(); break;
                default: g.fromFiles(); break;
            }
        } catch (Throwable ignored) {
        }
        if (g.minecraft == null) g.minecraft = versionFromClasspath();
        return g;
    }

    @Override
    public String toString() {
        String name = loader == null ? "?" : loader.substring(0, 1).toUpperCase() + loader.substring(1);
        return "Minecraft " + minecraft + ", " + name + (loaderVersion == null ? "" : " " + loaderVersion);
    }

    private void fromFabric() throws Exception {
        String quilt = fabricModVersion("quilt_loader");
        loader = quilt != null ? "quilt" : "fabric";
        loaderVersion = quilt != null ? quilt : fabricModVersion("fabricloader");
        minecraft = fabricModVersion("minecraft");
    }

    private static String fabricModVersion(String id) throws Exception {
        Class<?> api = Class.forName("net.fabricmc.loader.api.FabricLoader");
        Object instance = api.getMethod("getInstance").invoke(null);
        Optional<?> mod = (Optional<?>) api.getMethod("getModContainer", String.class).invoke(instance, id);
        if (!mod.isPresent()) return null;
        Object meta = Class.forName("net.fabricmc.loader.api.ModContainer").getMethod("getMetadata").invoke(mod.get());
        Object version = Class.forName("net.fabricmc.loader.api.metadata.ModMetadata").getMethod("getVersion").invoke(meta);
        return (String) Class.forName("net.fabricmc.loader.api.Version").getMethod("getFriendlyString").invoke(version);
    }

    private void fromForge() {
        loader = "forge";
        minecraft = staticString("net.minecraftforge.versions.mcp.MCPVersion", "getMCVersion");
        loaderVersion = staticString("net.minecraftforge.versions.forge.ForgeVersion", "getVersion");
        if (minecraft == null) minecraft = versionInfo("net.minecraftforge", "mcVersion");
        if (loaderVersion == null) loaderVersion = versionInfo("net.minecraftforge", "forgeVersion");
    }

    private void fromNeoForge() {
        loader = "neoforge";
        minecraft = versionInfo("net.neoforged", "mcVersion");
        loaderVersion = versionInfo("net.neoforged", "neoForgeVersion");
        if (loaderVersion == null) {
            loaderVersion = staticString("net.neoforged.neoforge.internal.versions.neoforge.NeoForgeVersion", "getVersion");
        }
        if (minecraft == null && loaderVersion != null) minecraft = minecraftFromNeoForge(loaderVersion);
    }

    private void fromBukkit() {
        loader = "vanilla";
        String v = staticString("org.bukkit.Bukkit", "getBukkitVersion");  // 1.20.1-R0.1-SNAPSHOT
        if (v != null) minecraft = v.split("-")[0];
    }

    /** Standalone mode: look at the server jar and the libraries/ folder next to it. */
    private void fromFiles() {
        File libs = new File("libraries");
        String neo = newestDir(new File(libs, "net/neoforged/neoforge"));
        String forge = newestDir(new File(libs, "net/minecraftforge/forge"));  // 1.20.1-47.2.0
        String fabric = newestDir(new File(libs, "net/fabricmc/fabric-loader"));
        String quilt = newestDir(new File(libs, "org/quiltmc/quilt-loader"));
        if (neo != null) {
            loader = "neoforge";
            loaderVersion = neo;
            minecraft = minecraftFromNeoForge(neo);
        } else if (forge != null && forge.contains("-")) {
            loader = "forge";
            minecraft = forge.substring(0, forge.indexOf('-'));
            loaderVersion = forge.substring(forge.indexOf('-') + 1);
        } else if (quilt != null) {
            loader = "quilt";
            loaderVersion = quilt;
        } else if (fabric != null) {
            loader = "fabric";
            loaderVersion = fabric;
        }
        if (minecraft == null) minecraft = versionFromJars(new File("."));
        if (loader == null && minecraft != null) loader = "vanilla";
    }

    /** NeoForge 20.4.237 is for Minecraft 1.20.4, 21.0.x for 1.21. */
    static String minecraftFromNeoForge(String version) {
        String[] p = version.split("\\.");
        if (p.length < 2 || !p[0].matches("\\d+") || !p[1].matches("\\d+")) return null;
        return "1." + p[0] + (p[1].equals("0") ? "" : "." + p[1]);
    }

    private static String versionInfo(String fmlPackage, String accessor) {
        try {
            Object info = Class.forName(fmlPackage + ".fml.loading.FMLLoader").getMethod("versionInfo").invoke(null);
            return (String) info.getClass().getMethod(accessor).invoke(info);
        } catch (Throwable t) {
            return null;
        }
    }

    private static String staticString(String className, String method) {
        try {
            return (String) Class.forName(className).getMethod(method).invoke(null);
        } catch (Throwable t) {
            return null;
        }
    }

    /** Every server jar since 1.14 has version.json at its root. */
    private static String versionFromClasspath() {
        for (ClassLoader cl : new ClassLoader[]{Thread.currentThread().getContextClassLoader(),
                GameInfo.class.getClassLoader()}) {
            if (cl == null) continue;
            try (InputStream in = cl.getResourceAsStream("version.json")) {
                if (in != null) return SharepackServer.jsonField(new String(SharepackServer.readAll(in), StandardCharsets.UTF_8), "id");
            } catch (IOException ignored) {
            }
        }
        return null;
    }

    private static String versionFromJars(File folder) {
        File[] jars = folder.listFiles((d, name) -> name.endsWith(".jar") && !name.startsWith("sharepack"));
        if (jars == null) return null;
        for (File jar : jars) {
            try (ZipFile zip = new ZipFile(jar)) {
                ZipEntry e = zip.getEntry("version.json");
                if (e != null) {
                    String id = SharepackServer.jsonField(new String(SharepackServer.readAll(zip.getInputStream(e)),
                            StandardCharsets.UTF_8), "id");
                    if (id != null) return id;
                }
            } catch (IOException ignored) {
            }
        }
        return null;
    }

    private static String newestDir(File d) {
        String[] names = d.list((parent, name) -> new File(parent, name).isDirectory());
        if (names == null || names.length == 0) return null;
        Arrays.sort(names, GameInfo::compareVersions);
        return names[names.length - 1];
    }

    private static int compareVersions(String a, String b) {
        if (SharepackServer.isNewer(a, b)) return 1;
        return SharepackServer.isNewer(b, a) ? -1 : 0;
    }
}
