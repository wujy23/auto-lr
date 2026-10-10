local LrPathUtils = import 'LrPathUtils'
local LrFileUtils = import 'LrFileUtils'
if LrFileUtils.exists(LrPathUtils.child(_PLUGIN.path, 'InstalledConfig.lua')) then
    return require 'InstalledConfig'
end
local projectRoot = LrPathUtils.parent(_PLUGIN.path)
return {
    root = LrPathUtils.child(LrPathUtils.child(projectRoot, 'work'), 'lr-link'),
    sessionSeconds = 1800,
}
