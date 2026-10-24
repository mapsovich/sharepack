package sharepack.server;

/** NeoForge 1.20.2+ (META-INF/neoforge.mods.toml, or mods.toml on early versions). */
@net.neoforged.fml.common.Mod("sharepack")
public class NeoForgeEntry {
    public NeoForgeEntry() {
        if (!SharepackServer.isClient("net.neoforged")) SharepackServer.start("neoforge");
    }
}
