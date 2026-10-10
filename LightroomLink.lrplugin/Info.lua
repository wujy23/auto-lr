return {
    LrSdkVersion = 6.0,
    LrSdkMinimumVersion = 6.0,
    LrToolkitIdentifier = 'local.codex.lightroomlink',
    LrPluginName = 'Lightroom Harness Link',
    LrShutdownPlugin = 'Stop.lua',
    LrLibraryMenuItems = {
        { title = 'Start connection', file = 'Start.lua' },
        { title = 'Stop connection', file = 'Stop.lua' },
    },
    VERSION = { major = 0, minor = 8, revision = 0, build = 0 },
}
