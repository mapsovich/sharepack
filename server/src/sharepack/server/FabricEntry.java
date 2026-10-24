package sharepack.server;

/** Fabric and Quilt (fabric.mod.json, environment = server). */
public class FabricEntry implements net.fabricmc.api.DedicatedServerModInitializer {
    @Override
    public void onInitializeServer() {
        SharepackServer.start("fabric");
    }
}
