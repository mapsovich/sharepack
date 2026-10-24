package sharepack.server;

/** Forge 1.13+ (META-INF/mods.toml). */
@net.minecraftforge.fml.common.Mod("sharepack")
public class ForgeEntry {
    public ForgeEntry() {
        if (!SharepackServer.isClient("net.minecraftforge")) SharepackServer.start("forge");
    }
}
