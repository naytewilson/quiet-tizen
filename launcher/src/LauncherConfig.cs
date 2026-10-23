using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

public sealed class LauncherInput
{
 [JsonPropertyName("id")] public int Id { get; set; }
 [JsonPropertyName("label")] public string Label { get; set; }
}

public sealed class LauncherConfig
{
 static readonly HashSet<string> AllowedSettings = new HashSet<string>(StringComparer.Ordinal)
 {
  "brightness", "volume", "mute", "game-mode", "sleep-timer", "advanced", "network", "bluetooth"
 };
 static readonly Regex AppIdPattern = new Regex(@"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\z", RegexOptions.CultureInvariant);
 static readonly HashSet<string> TopLevelFields = new HashSet<string>(StringComparer.Ordinal)
 {
  "inputs", "default_source", "viewer_app", "native_settings_app", "settings", "apps", "setting_options"
 };
 static readonly HashSet<string> EnumSettings = new HashSet<string>(StringComparer.Ordinal)
 {
  "game-mode", "sleep-timer"
 };
 static readonly Regex SettingOptionPattern = new Regex(@"\A[A-Za-z0-9_.-]{1,32}\z", RegexOptions.CultureInvariant);
 static readonly HashSet<string> InputFields = new HashSet<string>(StringComparer.Ordinal)
 {
  "id", "label"
 };

 [JsonPropertyName("inputs")] public List<LauncherInput> Inputs { get; set; } = new List<LauncherInput>();
 [JsonPropertyName("default_source")] public int? DefaultSource { get; set; }
 [JsonPropertyName("viewer_app")] public string ViewerApp { get; set; }
 [JsonPropertyName("native_settings_app")] public string NativeSettingsApp { get; set; }
 [JsonPropertyName("settings")] public List<string> Settings { get; set; } = new List<string>();
 [JsonPropertyName("apps")] public List<string> Apps { get; set; } = new List<string>();
 [JsonPropertyName("setting_options")] public Dictionary<string,List<string>> SettingOptions { get; set; }

 public static LauncherConfig Load(string path)
 {
  if(new FileInfo(path).Length>1048576)throw new InvalidDataException("Launcher config exceeds the 1 MiB bound.");
  string json=File.ReadAllText(path);
  using(var document=JsonDocument.Parse(json))
  {
   ValidateObjectFields(document.RootElement, TopLevelFields, "launcher config");
   if(!document.RootElement.TryGetProperty("inputs",out var inputs)||inputs.ValueKind!=JsonValueKind.Array
      ||!document.RootElement.TryGetProperty("default_source",out var defaultSource)
      ||defaultSource.ValueKind!=JsonValueKind.Number||!defaultSource.TryGetInt32(out _)
      ||!document.RootElement.TryGetProperty("viewer_app",out var viewerApp)
      ||viewerApp.ValueKind!=JsonValueKind.String
      ||!document.RootElement.TryGetProperty("native_settings_app",out var nativeSettingsApp)
      ||nativeSettingsApp.ValueKind!=JsonValueKind.String
      ||!document.RootElement.TryGetProperty("settings",out var settings)||settings.ValueKind!=JsonValueKind.Array
      ||!document.RootElement.TryGetProperty("apps",out var apps)||apps.ValueKind!=JsonValueKind.Array)
    throw new InvalidDataException("Launcher config must include inputs, source, viewer, native settings, settings and apps.");
   foreach(var input in inputs.EnumerateArray())
   {
    ValidateObjectFields(input,InputFields,"launcher input");
    if(!input.TryGetProperty("id",out var id)||id.ValueKind!=JsonValueKind.Number||!id.TryGetInt32(out _)
       ||!input.TryGetProperty("label",out var label)||label.ValueKind!=JsonValueKind.String)
     throw new InvalidDataException("Launcher input needs an ID and label.");
   }
   foreach(var setting in settings.EnumerateArray())
    if(setting.ValueKind!=JsonValueKind.String)throw new InvalidDataException("Launcher settings must be strings.");
   foreach(var app in apps.EnumerateArray())
    if(app.ValueKind!=JsonValueKind.String)throw new InvalidDataException("Launcher app IDs must be strings.");
   if(apps.GetArrayLength()>64)throw new InvalidDataException("Launcher app allow-list exceeds the 64-app cache bound.");
   if(document.RootElement.TryGetProperty("setting_options",out var settingOptions))
   {
    if(settingOptions.ValueKind!=JsonValueKind.Object)throw new InvalidDataException("Setting options must be an object.");
    var seen=new HashSet<string>(StringComparer.Ordinal);
    foreach(var property in settingOptions.EnumerateObject())
    {
     if(!EnumSettings.Contains(property.Name)||!seen.Add(property.Name)||property.Value.ValueKind!=JsonValueKind.Array)
      throw new InvalidDataException("Invalid or duplicate setting option map.");
     if(property.Value.GetArrayLength()<2||property.Value.GetArrayLength()>32)
      throw new InvalidDataException("Setting option lists must contain 2..32 values.");
     var values=new HashSet<string>(StringComparer.Ordinal);
     foreach(var option in property.Value.EnumerateArray())
      if(option.ValueKind!=JsonValueKind.String||!SettingOptionPattern.IsMatch(option.GetString())
         ||!values.Add(option.GetString()))
       throw new InvalidDataException("Setting option values must be valid and unique strings.");
    }
   }
  }
  var config=JsonSerializer.Deserialize<LauncherConfig>(json);
  if(config==null)throw new InvalidDataException("Launcher config is empty.");
  config.Validate();
  return config;
 }

 static void ValidateObjectFields(JsonElement element,HashSet<string> allowed,string context)
 {
  if(element.ValueKind!=JsonValueKind.Object)throw new InvalidDataException("Invalid "+context+" object.");
  var seen=new HashSet<string>(StringComparer.Ordinal);
  foreach(var property in element.EnumerateObject())
   if(!allowed.Contains(property.Name)||!seen.Add(property.Name))
    throw new InvalidDataException("Unknown or duplicate "+context+" field.");
 }

 public void Validate()
 {
  if(!IsValidAppId(ViewerApp)||!IsValidAppId(NativeSettingsApp))
   throw new InvalidDataException("Viewer and native settings targets must be validated app IDs.");
  if(Inputs==null||Inputs.Count<1||Inputs.Count>3)throw new InvalidDataException("Configure one to three observed inputs.");
  var sourceIds=new HashSet<int>();
  foreach(var input in Inputs)
  {
   if(input==null||input.Id<0||input.Id>65535||!sourceIds.Add(input.Id)
      ||string.IsNullOrWhiteSpace(input.Label)||input.Label.Length>80)
    throw new InvalidDataException("Invalid launcher input.");
   foreach(char c in input.Label)if(char.IsControl(c))throw new InvalidDataException("Invalid input label.");
  }
  if(!DefaultSource.HasValue||!sourceIds.Contains(DefaultSource.Value))
   throw new InvalidDataException("Default source must name a configured input.");
  if(Settings==null||Apps==null)throw new InvalidDataException("Settings and app lists must be arrays.");
  var settings=new HashSet<string>(StringComparer.Ordinal);
  foreach(string setting in Settings)
   if(string.IsNullOrEmpty(setting)||!AllowedSettings.Contains(setting)||!settings.Add(setting))
    throw new InvalidDataException("Unsupported or duplicate launcher setting.");
  var apps=new HashSet<string>(StringComparer.Ordinal);
  if(Apps.Count>64)throw new InvalidDataException("Launcher app allow-list exceeds the 64-app cache bound.");
  foreach(string app in Apps)
   if(!IsValidAppId(app)||!apps.Add(app))throw new InvalidDataException("Invalid or duplicate catalog app ID.");
  if(SettingOptions==null)SettingOptions=new Dictionary<string,List<string>>(StringComparer.Ordinal);
  foreach(var item in SettingOptions)
  {
   if(!EnumSettings.Contains(item.Key)||!settings.Contains(item.Key)||item.Value==null
      ||item.Value.Count<2||item.Value.Count>32)
    throw new InvalidDataException("Setting option maps must target configured enum settings.");
   var options=new HashSet<string>(StringComparer.Ordinal);
   foreach(string option in item.Value)
    if(option==null||!SettingOptionPattern.IsMatch(option)||!options.Add(option))
     throw new InvalidDataException("Invalid or duplicate setting option.");
  }
  foreach(string name in EnumSettings)
   if(settings.Contains(name)&&!SettingOptions.ContainsKey(name))
    throw new InvalidDataException("Configured enum settings require an observed value map.");
 }

 public bool HasSource(int id)
 {
  foreach(var input in Inputs)if(input.Id==id)return true;
  return false;
 }

 public bool HasSetting(string name)=>Settings.Contains(name);

 public bool TryNextSettingValue(string name,string current,out string next)
 {
  next=null;
  if(!HasSetting(name)||SettingOptions==null||!SettingOptions.TryGetValue(name,out var options)
     ||options==null||options.Count<2)return false;
  int index=options.IndexOf(current);
  if(index<0)return false;
  next=options[(index+1)%options.Count];
  return true;
 }

 public static bool ValidBrightnessRange(double min,double max,double step)
  =>double.IsFinite(min)&&double.IsFinite(max)&&double.IsFinite(step)&&min<max&&step>0&&step<=max-min;

 public static bool IsValidAppId(string value)=>value!=null&&AppIdPattern.IsMatch(value);
}
