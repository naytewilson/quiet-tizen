# DU7200 native AV and remote-control acceptance matrix

**Current result: NO-GO. No hardware tests were executed.** This matrix
defines the evidence required to determine whether a future launcher change
preserves native TV behavior. The TV's exact current model, firmware,
equipment, baseline settings, and connected services are UNKNOWN here. Every
baseline, post-install, and restored result in
evidence/du7200-acceptance.json is currently NOT_TESTED. A missing baseline
is never a PASS.

Samsung's developer guidance recommends testing on the target TV because the
hardware environment can differ from an emulator. Samsung's support guidance
also says picture menus differ by model/year, Game Mode is limited to select
models, CEC and ARC/eARC require compatible connected equipment, and output
audio formats depend on the device and connection. Those general pages do not
prove a DU7200 feature or exact port capability. [Samsung TV Device testing](https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/tv-device.html),
[Samsung picture settings](https://www.samsung.com/us/support/answer/ANS10006970/),
[Samsung Game Mode](https://www.samsung.com/ca/support/tv-audio-video/use-game-mode-on-your-samsung-smart-tv/),
[Samsung Anynet+ CEC](https://www.samsung.com/ca/support/tv-audio-video/anynet-hdmi-cec-on-your-samsung-tv/),
[Samsung ARC/eARC](https://www.samsung.com/us/support/answer/ANS10006962/),
[Samsung audio formats](https://www.samsung.com/us/support/answer/ANS10005199/)

## Status and evidence rules

Use only these dispositions:

- **NOT_TESTED**: no fresh test was performed on this exact TV/firmware and
  stage. This is the current value for every row.
- **UNKNOWN**: available evidence cannot establish support, configuration,
  or outcome.
- **PASS**: the recorded expected behavior was directly observed for this
  exact model/firmware/stage and has a private timestamped evidence reference.
- **NOT_SUPPORTED**: direct evidence from the target proves the capability is
  unavailable. This is a measured result, not an assumption based on model
  family or marketing literature.
- **FAIL**: the expected behavior regressed. Stop the next phase and recover.

Each PASS or NOT_SUPPORTED record needs PROVEN evidence with method
actual-hardware, source, observed_at, exact model and exact firmware.
Record the source device, port, signal mode, relevant TV menu values and
equipment used without capturing account credentials or private content.
Store detailed evidence privately; keep only sanitized metadata in the
repository. Do not put addresses, serial numbers, MAC addresses, passwords,
pairing secrets, login data, session tokens, or raw databases into the matrix.

The checker at tools/check_acceptance.py requires each declared row to have a
baseline, post_install, and restored result. It returns NO-GO (exit 1) for
missing rows, NOT_TESTED/UNKNOWN/SKIP, absent evidence, or evidence bound to a
different model/firmware. NOT_SUPPORTED still requires actual-hardware
evidence. A source fixture or screenshot from another TV cannot satisfy this
gate. Observable function does not measure latency, luminance/chrominance,
HDR accuracy, frame pacing, upscaling fidelity, audio sync, or codec fidelity.

## Equipment and controlled baseline

Before a future change, retain a private equipment and topology sheet:

- Exact model and firmware from About This TV and supported read-only
  inventory; panel size/region as shown on the physical unit; date/time.
- TV remote, any physical controls used, all HDMI devices and ports, cable
  type/length where known, receiver/soundbar/headphones, Bluetooth devices,
  wired network switch/router, and the existing authorized remote client.
- Source device model and firmware, its output settings, app/source content,
  and the actual resolution, refresh, color/HDR mode it reports. Do not
  identify an HDMI port by another unit's numbering.
- Test content: known SDR and HDR material from a local HDMI source, native
  streaming services already signed in, and repeatable picture/motion/audio
  samples. Keep the same source/content and settings before and after.
- Optional measurement equipment: HDMI signal analyzer/capture for negotiated
  format; colorimeter or spectroradiometer for luminance/color; photodiode or
  high-speed camera and repeatable input stimulus for input-to-photon latency;
  and audio capture/meter with a known sync pattern. Availability is currently
  UNKNOWN. A phone photo or visible mode badge is functional evidence only,
  not an accuracy or latency measurement.

Record the TV's existing picture and sound menu values without resetting them.
Keep room lighting, source, cable, port, app, content, audio device, network
topology, and client device consistent. Do not sign out of streaming services,
reset Smart Hub, clear app data, unpair devices, rename inputs, or change
calibration presets to simplify the test. If the pre-change baseline cannot
be observed or the setup changes, leave baseline NOT_TESTED.

## Required capability rows

For every row, repeat the same steps at three stages: baseline before any
change; post_install after each proposed launcher phase; and restored after
the exact inverse is applied. Record the outcomes independently. Do not
convert a menu item being visible into PASS for an end-to-end behavior.

| Required ID | Baseline and post-install observation | Equipment / limits | Recovery check |
|---|---|---|---|
| picture-4k-upscaling | Play the same known 4K and lower-resolution SDR clip from the same source. Record source-negotiated format, picture-size/aspect mode, overscan/cropping, visible detail and any change in native picture settings. | Known 4K source and repeatable 1080p/720p clips; HDMI analyzer for exact signal, meter/reference display if claiming fidelity. Visual inspection does not quantify scaling. | Restore the exact prior picture-size and enhancement values; retest both source resolutions. |
| picture-hdr | Play known SDR and HDR content from HDMI and already-signed-in streaming apps. Record HDR mode indication, input signal, picture preset and whether the TV returns to SDR correctly. | HDR10-capable known source/content and analyzer if claiming HDR signaling; meter for color/luminance accuracy. No model support inferred. | Restore recorded HDR/picture settings and verify SDR/HDR transitions. |
| picture-modes-advanced | Open the full native Picture and Expert Settings screens; record every exposed picture mode and relevant mode/settings availability before and after. Check the active source's settings are retained. | Existing TV remote; camera or notes for menu values. Available menus vary by model and year. | Verify original picture mode and values; ensure native settings remain reachable after launcher crash. |
| gaming-game-mode-allm | With each available console/PC source, inspect Game Mode Off/Auto/On behavior, source detection, available Game Bar/status, ALLM indication if exposed, and return to the original picture state. Mark each unavailable feature UNKNOWN until verified. | Actual connected console/PC, source menu and TV mode status; HDMI analyzer for ALLM signaling. A mode label alone does not measure input lag. | Restore the original Game Mode setting; verify native picture options and source selection. |
| gaming-console-input | For each console, cold-start and wake it, select its actual input, navigate, launch a game, suspend/return, and verify picture/audio/control. Record exact port and source label. | Each console actually owned/connected and its known-good HDMI cable. Do not claim 4K120 or VRR from a marketing page. | Restore original input, name and Game Mode settings; retest console wake and return to TV. |
| audio-internal-volume-mute | Verify internal speakers with repeatable dialog/music, volume steps, mute/unmute, power/wake and return from an app. Record Sound Output and Sound Mode values. | Existing remote, known repeatable audio clip; meter if reporting measured level. | Restore baseline speaker selection, mode, volume and mute state. |
| audio-modes-sync | Check available Sound Mode, digital output format, audio delay/sync and speech/dialog settings with the same content. Use a sync pattern to assess audio/video alignment; record if no measurement equipment exists. | Repeatable sync clip; audio capture or meter for measured delay. Listening alone is not millisecond evidence. | Restore original sync/delay and sound settings; verify app and HDMI playback. |
| audio-external-formats | For each existing receiver/soundbar/headphone route, test compatible PCM, bitstream/Dolby or other options actually exposed. Verify decoding, channel count, volume/mute and return to internal speakers. | The owner's actual connected equipment and known formats; analyzer/receiver status for format claims. | Restore original output route/format; verify internal speakers still work. |
| hdmi-every-port | Inventory each physical HDMI port individually. For every connected source, record port number physically observed, source name, negotiated resolution/refresh/color mode, picture and audio, and wake/return. | All existing source devices and cables; note empty/unavailable ports NOT_TESTED. Do not infer port mapping from another model. | Restore each previous input selection and source naming; verify all connected ports again. |
| hdmi-cec-naming | For each CEC-capable device, verify Anynet+/CEC discovery, the device name shown, remote navigation, power on/off and source switching. Record disabled/unavailable devices as UNKNOWN until checked. | Existing HDMI devices that implement CEC; TV remote and device status. CEC behavior can be device-specific. | Restore saved Anynet+/CEC and names exactly; confirm each existing device remains reachable. |
| hdmi-hotplug | With each existing HDMI device, test disconnect/reconnect or controlled power cycle, source discovery and recovery to TV/native UI. Avoid unplugging a device during firmware update or active recording. | Existing HDMI devices and accessible cables; no guessed port assumptions. | Restore normal cabling and input selection; confirm picture/audio and CEC discovery. |
| hdmi-arc-support | Check the actual ARC-labeled port and menu option, if present, with the owner's compatible audio system. Record ARC specifically. eARC remains UNKNOWN unless this unit and receiver expose and negotiate it. | Actual receiver/soundbar and known-good HDMI cable; analyzer/receiver status for mode claims. | Restore prior audio route and CEC setting; verify TV speakers and external audio. |
| bluetooth-audio | Inspect native Bluetooth device list, existing pairings, remote control and audio connection. Test only a device already authorized for this TV; do not remove/re-pair it. | Existing Bluetooth devices and TV remote; observe connection and audio. | Ensure each baseline pairing remains and reconnects without deleting stored credentials. |
| network-apps-discovery | From the TV's existing wired or wireless network, verify app connectivity, DNS/internet access, native network settings and the previously used device-discovery path. Record wired/wireless topology privately. | Existing router/switch and authorized devices; no credentials in logs. | Verify original network mode/address path and discovery after restore. |
| streaming-drm-logins-hdr | Open each streaming app already in use; verify retained sign-in, protected playback, pause/resume, seek, audio, and SDR/HDR indication where content provides it. Do not sign out or clear data. | Existing subscribed apps and known available test content. Do not record account details or capture protected video. | Confirm same accounts remain signed in and playback/DRM/HDR behavior returns after restore. |
| native-settings-crash-fallback | Open Picture, Sound, Network, General, External Device Manager, Support, Accessibility, Bluetooth and app settings as exposed by this unit. Repeat after deliberately closing/crashing only the test launcher if an authorized reversible method exists. Verify Home/Back returns to native settings. | TV remote or existing approved remote client; preserve a working recovery control. A crash fallback must not depend on the launcher. | Restore the original launcher phase and verify all native settings screens remain reachable. |
| lifecycle-coldboot-restart | After each approved phase, perform a full normal power restart and verify native boot, Home, startup app, apps, settings, audio, network and remote reconnection. | TV and normal remote; observe full cold boot and startup outcome. | Verify baseline boot behavior, selector values and app availability after inverse. |
| lifecycle-standby-wake | Enter ordinary standby, wait, wake with the remote and with each existing authorized wake source. Verify image, sound, input, network and remote session return. | TV remote and existing devices; no factory/service-menu actions. | Verify baseline wake state and network remote reconnect. |
| lifecycle-switch-reconnect-crash | Switch among TV/Home, HDMI and an existing streaming app; test Back/Home, suspend/resume, launcher close/crash fallback and SDB/network reconnect. | Existing inputs and apps. Retain private time-stamped logs; functional check does not quantify timing. | Verify original navigation paths and recovery control after exact inverse. |
| wifi-remote-navigation-home-back | With the TV connected by Ethernet and the existing authorized phone/tablet remote on Wi-Fi, exercise navigation, Home and Back before and after each phase. Confirm TV state responds and the client remains paired. | Existing remote app and current Wi-Fi; no new pairing. Samsung documents these controls generally, but exact availability varies by device. | Reconnect from the same client after restore without re-pairing; verify Home/Back. |
| wifi-remote-volume-mute-power | From that existing Wi-Fi client, test volume up/down, mute/unmute, and supported power/standby actions before and after; record any app/device-specific limitation. | Existing authorized remote client; TV must remain wired for this check. | Confirm controls and mute/volume state return; reconnect after standby/reboot without pairing. |
| wifi-remote-app-input | From the same client, launch an already-installed app and switch to every relevant HDMI/input before and after; return to Home and verify source. | Existing authorized remote app, installed app and connected HDMI sources. Do not change app sign-in or invent source IDs. | Verify app launch/input selection and native return after exact inverse. |
| wifi-remote-reconnect-no-repair | Verify the existing network-remote server, listener, discovery, WebSocket and JSON request/response path before and after. Reboot and standby/wake the wired TV, reconnect the Wi-Fi client with the same pairing credentials, and repeat navigation/volume/mute/power/app/input controls without re-pair. | Existing authorized Wi-Fi phone/tablet client, wired TV, current network equipment and private server evidence. Preserve pairing credentials; never copy secrets into evidence. | Restore only the changed app/server phase; ensure the same client reconnects and controls the TV without a new pairing flow. |

## Explicit unknowns and production gate

Every hardware evidence reference must include its full `artifact_sha256` and
a timezone-aware `observed_at` within the latest 24 hours. Reacquire evidence
after drift or expiry. The checker validates records; an assertion and hash
do not authenticate an observation. Independently inspect the private artifact
and actual hardware before relying on a GO record.

For `wifi-remote-reconnect-no-repair`, every stage also requires structured
`evidence.remote_pairing`: `repaired: false`, `credentials_unchanged: true`,
`reconnected_after_reboot: true`, `reconnected_after_standby: true`, and
`topology: "ethernet-tv_wifi-client"`. Record preservation without copying
credentials into the report. This critical row cannot use NOT_SUPPORTED to
waive the existing network-remote path.

At this writing the following are UNKNOWN: exact current firmware and
equipment; 4K input/output modes and upscaling behavior; HDR formats; picture
presets and motion processing; Game Mode, ALLM, VRR, 120 Hz, and measured
latency; internal/external speaker behavior, audio sync and supported formats;
every HDMI port, CEC naming and hotplug behavior; ARC/eARC; Bluetooth; wired
TV/Wi-Fi remote-client compatibility; discovery and remote server details;
streaming app sign-in retention, DRM and HDR; native settings after launcher
crash; cold boot, restart, standby, wake and reconnect.

VRR, 120 Hz, eARC and ALLM must remain UNKNOWN until the exact port/source/
receiver path is inspected and its negotiated state is proven on this unit.
Samsung's generic compatibility information is not evidence that any one of
these capabilities is present here. Functional menu/control checks do not
prove input-to-photon latency, picture fidelity, HDR accuracy, frame pacing,
audio sync or codec equivalence.

The release checker requires all 23 capability rows at baseline,
post_install, and restored stages. Use:

    python3 tools/check_acceptance.py evidence/du7200-acceptance.json --baseline-only
    python3 tools/check_acceptance.py evidence/du7200-acceptance.json

With the current null firmware and NOT_TESTED stages, both calls must return
NO-GO (exit status 1). Production remains NO-GO until the full checker reports
GO with current exact model/firmware and verified hardware evidence for every
stage. A capability with no baseline remains NOT_TESTED.
