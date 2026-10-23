using System;
using System.IO;

static class CatalogTests
{
    static void Equal(int expected, int actual, string context)
    {
        if (expected != actual) throw new Exception(context + ": expected " + expected + ", got " + actual);
    }
    static void RejectsLauncherConfig(string json, string context)
    {
        string tmp=Path.GetTempFileName();
        try
        {
            File.WriteAllText(tmp,json);
            bool rejected=false;
            try { LauncherConfig.Load(tmp); } catch (InvalidDataException) { rejected=true; }
            if(!rejected)throw new Exception("Invalid launcher config accepted: "+context);
        }
        finally { File.Delete(tmp); }
    }
    static void TestLauncherConfig()
    {
        string valid="{\"inputs\":[{\"id\":14,\"label\":\"Console\"},{\"id\":15,\"label\":\"Switch\"}],"
            +"\"default_source\":14,\"viewer_app\":\"org.tizen.tv-viewer\",\"native_settings_app\":\"org.tizen.menu\","
            +"\"settings\":[\"brightness\",\"game-mode\",\"advanced\"],"
            +"\"setting_options\":{\"game-mode\":[\"off\",\"automatic\",\"on\"]},"
            +"\"apps\":[\"org.tizen.netflix-app\"]}";
        string tmp=Path.GetTempFileName();
        try
        {
            File.WriteAllText(tmp,valid);
            var config=LauncherConfig.Load(tmp);
            Equal(2,config.Inputs.Count,"configured inputs");
            if(config.ViewerApp!="org.tizen.tv-viewer"||config.NativeSettingsApp!="org.tizen.menu")
                throw new Exception("Profile-derived viewer and native settings targets were not loaded");
            if(config.DefaultSource!=14||!config.HasSource(15)||config.HasSource(13))
                throw new Exception("Launcher source allow-list was not applied");
            if(!config.HasSetting("brightness")||config.HasSetting("network"))
                throw new Exception("Launcher settings allow-list was not applied");
            if(config.Apps.Count!=1||config.Apps[0]!="org.tizen.netflix-app")
                throw new Exception("Launcher app IDs were not loaded");
            if(!config.TryNextSettingValue("game-mode","off",out var next)||next!="automatic"
               ||!config.TryNextSettingValue("game-mode","on",out next)||next!="off"
               ||config.TryNextSettingValue("game-mode","unexpected",out next)
               ||config.TryNextSettingValue("sleep-timer","0",out next))
                throw new Exception("Private setting choices did not respect the exact observed mapping");
            if(!LauncherConfig.ValidBrightnessRange(0,50,1)
               ||LauncherConfig.ValidBrightnessRange(0,50,0)
               ||LauncherConfig.ValidBrightnessRange(50,0,1)
               ||LauncherConfig.ValidBrightnessRange(0,double.NaN,1)
               ||LauncherConfig.ValidBrightnessRange(0,50,double.PositiveInfinity))
                throw new Exception("Invalid brightness range was treated as editable");
        }
        finally { File.Delete(tmp); }

        string common=",\"viewer_app\":\"org.tizen.tv-viewer\",\"native_settings_app\":\"org.tizen.menu\"";
        RejectsLauncherConfig("{\"inputs\":[{\"id\":14,\"label\":\"A\"}],\"default_source\":14"+common+",\"settings\":[\"game-mode\"],\"apps\":[]}","missing observed enum options");
        RejectsLauncherConfig("{\"inputs\":[{\"id\":14,\"label\":\"A\"},{\"id\":14,\"label\":\"B\"}],\"default_source\":14"+common+",\"settings\":[],\"apps\":[]}","duplicate input");
        RejectsLauncherConfig("{\"inputs\":[{\"id\":14,\"label\":\"A\"}],\"default_source\":15"+common+",\"settings\":[],\"apps\":[]}","unknown default source");
        RejectsLauncherConfig("{\"inputs\":[{\"id\":14,\"label\":\"A\"}],\"default_source\":14,\"settings\":[],\"apps\":[]}","missing native app targets");
        RejectsLauncherConfig("{\"inputs\":[{\"id\":14,\"label\":\"A\"}],\"default_source\":14.5"+common+",\"settings\":[],\"apps\":[]}","fractional source ID");
        RejectsLauncherConfig("{\"inputs\":[{\"id\":1,\"label\":\"A\"},{\"id\":2,\"label\":\"B\"},{\"id\":3,\"label\":\"C\"},{\"id\":4,\"label\":\"D\"}],\"default_source\":1"+common+",\"settings\":[],\"apps\":[]}","too many inputs");
        RejectsLauncherConfig("{\"inputs\":[{\"id\":14,\"label\":\"A\"}],\"default_source\":14"+common+",\"settings\":[\"CSharpSetItemValue\"],\"apps\":[]}","unlisted settings API");
        RejectsLauncherConfig("{\"inputs\":[{\"id\":14,\"label\":\"A\"}],\"default_source\":14"+common+",\"settings\":[true],\"apps\":[]}","non-string setting");
        RejectsLauncherConfig("{\"inputs\":[{\"id\":14,\"label\":\"A\"}],\"default_source\":14"+common+",\"settings\":[],\"apps\":[true]}","non-string app ID");
        RejectsLauncherConfig("{\"inputs\":[{\"id\":14,\"label\":\"A\",\"source\":15}],\"default_source\":14"+common+",\"settings\":[],\"apps\":[]}","unknown input field");
        RejectsLauncherConfig("{\"inputs\":[{\"label\":\"A\"}],\"default_source\":0"+common+",\"settings\":[],\"apps\":[]}","missing input ID");
        RejectsLauncherConfig("{\"inputs\":[{\"id\":14,\"label\":\"A\"}],\"default_source\":14"+common+",\"settings\":[],\"apps\":[\"../outside\"]}","unsafe app ID");
        RejectsLauncherConfig("{\"inputs\":[{\"id\":14,\"label\":\"A\"}],\"default_source\":14,\"viewer_app\":\"../outside\",\"native_settings_app\":\"org.tizen.menu\",\"settings\":[],\"apps\":[]}","unsafe viewer app ID");
        RejectsLauncherConfig("{\"inputs\":[{\"id\":14,\"label\":\"A\"}],\"default_source\":14,\"viewer_app\":\"org.tizen.tv-viewer\",\"native_settings_app\":\"../outside\",\"settings\":[],\"apps\":[]}","unsafe native settings app ID");
    }
    static void TestConfiguredCatalogFilter()
    {
        string tmp=Path.GetTempFileName();
        try
        {
            File.WriteAllText(tmp,"["
                +"{\"Name\":\"A\",\"Id\":\"org.example.a\",\"Asset\":\"a.svg\",\"Width\":1,\"Height\":1},"
                +"{\"Name\":\"B\",\"Id\":\"org.example.b\",\"Asset\":\"b.svg\",\"Width\":1,\"Height\":1},"
                +"{\"Name\":\"Store\",\"Id\":\"org.volt.apps\",\"Asset\":\"store.svg\",\"Width\":1,\"Height\":1,\"Utility\":true}]");
            var filtered=AppLibrary.Load(tmp,new[] {"org.example.b"});
            Equal(1,filtered.Count,"configured catalog app count");
            if(filtered[0].Id!="org.example.b")
                throw new Exception("Catalog included an app outside the configured allow-list");
            Equal(0,AppLibrary.Load(tmp,new string[0]).Count,"empty app allow-list");
            bool rejected=false;
            try { AppLibrary.Load(tmp,new[] {"org.example.missing"}); } catch(InvalidDataException) { rejected=true; }
            if(!rejected)throw new Exception("Unknown configured app ID accepted");
        }
        finally { File.Delete(tmp); }
    }
    public static int Main(string[] args)
    {
        var catalog = AppLibrary.Load(args[0]);
        if (catalog.Count < 2) throw new Exception("Catalog unexpectedly small");
        Equal(-1, AppLibrary.Move(31, 0, "Up"), "top row returns to tabs");
        Equal(15, AppLibrary.Move(31, 10, "Down"), "last row enters next page");
        Equal(25, AppLibrary.Move(31, 30, "Up"), "partial page returns to matching previous column");
        Equal(30, AppLibrary.Move(31, 29, "Down"), "last partial page clamps column");
        Equal(11, AppLibrary.Move(12, 9, "Down"), "partial bottom row clamps column");
        Equal(11, AppLibrary.Move(12, 11, "Right"), "last item remains selected");
        Equal(-1, AppLibrary.Move(0, 0, "Down"), "empty catalog");
        for (int count = 1; count < 48; count++)
            for (int item = 0; item < count; item++)
                foreach (var key in new[] {"Left", "Right", "Up", "Down"})
                {
                    int next = AppLibrary.Move(count, item, key);
                    if (next < -1 || next >= count) throw new Exception("Out of bounds navigation");
                }
        string tmp = Path.GetTempFileName();
        try
        {
            foreach (string json in new[] {
                "[{\"Name\":\"Bad\",\"Id\":\"one\",\"Asset\":\"../escape.svg\",\"Width\":100,\"Height\":50}]",
                "[{\"Name\":\"Bad\",\"Id\":\"one\",\"Asset\":\"ok.svg\",\"Width\":100,\"Height\":50,\"Crop\":[0.8,0,0.4,1]}]",
                "[{\"Name\":\"A\",\"Id\":\"one\",\"Asset\":\"ok.svg\",\"Width\":100,\"Height\":50},{\"Name\":\"B\",\"Id\":\"one\",\"Asset\":\"ok.svg\",\"Width\":100,\"Height\":50}]"
            })
            {
                File.WriteAllText(tmp, json);
                bool rejected = false;
                try { AppLibrary.Load(tmp); } catch (InvalidDataException) { rejected = true; }
                if (!rejected) throw new Exception("Invalid catalog accepted");
            }
        }
        finally { File.Delete(tmp); }
        TestLauncherConfig();
        TestConfiguredCatalogFilter();
        Console.WriteLine("Catalog, launcher config and navigation checks passed.");
        return 0;
    }
}
