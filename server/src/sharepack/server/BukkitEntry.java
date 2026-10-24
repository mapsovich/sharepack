package sharepack.server;

/** Paper / Spigot (plugin.yml). */
public class BukkitEntry extends org.bukkit.plugin.java.JavaPlugin {
    @Override
    public void onEnable() {
        SharepackServer.logger = getLogger()::info;
        SharepackServer.start("bukkit");
    }
}
