"""Compile/run the bridge with mock SDK objects in the installed Lua runtime.

Never starts the plug-in or accesses a live catalog. Optional on other computers.
"""
import ctypes
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main as h

LIB = Path('/Applications/Adobe Lightroom Classic/Adobe Lightroom Classic.app/Contents/Frameworks/AgKernel.framework/Versions/A/AgKernel')
BRIDGE = h.HERE / 'LightroomLink.lrplugin' / 'Bridge.lua'


@unittest.skipUnless(LIB.exists() and BRIDGE.exists(), 'Installed Adobe Lua runtime/bridge absent')
class LuaBridgeTests(unittest.TestCase):
    def test_import_reuse_catalog_guard_export_flags_and_json_arrays(self):
        lib = ctypes.CDLL(str(LIB))
        lib.luaL_newstate.restype = ctypes.c_void_p
        lib.luaL_openlibs.argtypes = [ctypes.c_void_p]
        lib.luaL_loadbuffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]
        lib.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        lib.lua_tolstring.restype = ctypes.c_char_p
        lib.lua_close.argtypes = [ctypes.c_void_p]
        state = lib.luaL_newstate()
        lib.luaL_openlibs(state)
        setup = r'''
local settings = {Exposure2012=0, ToneCurvePV2012={0,0,255,255}}
local photos, active, imports, rendered, deleted = {}, nil, 0, nil, false
local mutateRender = false
local activeFolder, pendingFolder, settleTicks, blocked = nil, nil, 0, false
local sourceFolder = {}
local ticks, selectionCalls, onSleep = 0, 0, nil
local catalog = {}
function catalog:getPath() return '/mock/catalog' end
function catalog:getTargetPhoto() return active end
function catalog:getTargetPhotos() return active and {active} or {} end
function catalog:findPhotoByPath(path) return photos[path] end
function catalog:withWriteAccessDo(name,fn,opts) fn(); return 'executed' end
function catalog:addPhoto(path)
    imports=imports+1
    local p={localIdentifier=42}
    function p:getRawMetadata(key)
        if key=='path' then return path elseif key=='fileFormat' then return 'RAW' else return false end
    end
    function p:getFormattedMetadata(key) return 'input.NEF' end
    function p:getDevelopSettings() return settings end
    function p:checkPhotoAvailability() return true end
    photos[path]=p
    return p
end
function catalog:getFolderByPath(path) assert(path=='/mock'); return sourceFolder end
function catalog:setActiveSources(sources)
    assert(sources[1]==sourceFolder)
    pendingFolder=sources[1]; settleTicks=3
    return true
end
function catalog:setSelectedPhotos(p,others)
    selectionCalls=selectionCalls+1
    if activeFolder==sourceFolder and not blocked then active=p end
end
local modules={
 LrApplication={activeCatalog=function() return catalog end},
 LrTasks={sleep=function()
    ticks=ticks+1
    settleTicks=settleTicks-1
    if settleTicks<=0 then activeFolder=pendingFolder end
    if onSleep then onSleep() end
 end},
 LrFileUtils={exists=function(path)
                if path:match('InstalledConfig.lua$') then return false end
                return 'file'
              end, createAllDirectories=function() end,
              delete=function() deleted=true end},
 LrPathUtils={isAbsolute=function(p) return p:sub(1,1)=='/' end,
              parent=function(p) return p:match('^(.*)/[^/]+$') end,
              child=function(a,b) return a..'/'..b end},
 LrExportSession=function(spec)
    rendered=spec.exportSettings
    return {renditions=function()
        local used=false
        return function()
            if used then return nil end
            used=true
            return 1,{waitForRender=function()
                if mutateRender then settings.Exposure2012=9 end
                return true,'/mock/output.jpg'
            end}
        end
    end}
 end,
}
function import(name) assert(modules[name],name); return modules[name] end
local function hex(s) return (s:gsub('.',function(c) return string.format('%02x',string.byte(c)) end)) end
'''
        setup += "\n_PLUGIN={path='/relocated project/LightroomLink.lrplugin'}\npackage.preload.Config=function()\n"
        setup += (BRIDGE.parent / 'Config.lua').read_text() + '\nend\n'
        setup += "assert(require('Config').root=='/relocated project/work/lr-link')\n"
        source = BRIDGE.read_text().rsplit('return M', 1)[0]
        assertions = r'''
assert(json({11,22,33})=='[11,22,33]')
assert(json({name='x'})=='{"name":"x"}')
-- A first successful selection is cleared by delayed source refresh.
onSleep=function() if ticks==6 then active=nil end end
local selected1=preparePhoto('',hex('/mock/input.NEF'),'-')
assert(selected1.imported==true and imports==1 and selected1.photoId=='42')
assert(ticks>=26, 'prepare returned before selection stabilized after refresh')
onSleep=nil
-- Subsequent reads tolerate a transient empty target without reselecting.
local intended=active
local callsBefore=selectionCalls
local start=ticks
active=nil
onSleep=function() if ticks-start==3 then active=intended end end
assert(inspect('42').photoId=='42')
assert(selectionCalls==callsBefore)
-- A different photo appearing during that wait is never accepted or replaced.
start=ticks; active=nil
onSleep=function() if ticks-start==2 then active={localIdentifier=99} end end
local wrongOk,wrongError=pcall(function() inspect('42') end)
assert(not wrongOk and tostring(wrongError):find('Selected photo changed'))
assert(selectionCalls==callsBefore and active.localIdentifier==99)
-- A permanently empty selection has a bounded wait and actionable error.
onSleep=nil; active=nil; start=ticks
local emptyOk,emptyError=pcall(function() inspect('42') end)
assert(not emptyOk and tostring(emptyError):find('No active photo after waiting'))
assert(ticks-start==20 and selectionCalls==callsBefore)
active=intended
local selected2=preparePhoto('42',hex('/mock/input.NEF'),hex('/mock/catalog'))
assert(selected2.imported==false and imports==1)
assert(not pcall(function() preparePhoto('',hex('/mock/input.NEF'),hex('/wrong/catalog')) end))
active=nil; blocked=true
local selectionOk,selectionError=pcall(function() preparePhoto('',hex('/mock/input.NEF'),'-') end)
assert(not selectionOk and tostring(selectionError):find('Auto%-selection failed'))
assert(imports==1)
blocked=false
preparePhoto('',hex('/mock/input.NEF'),'-')
local baseline=batch.settings('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','42')
local exported=batch.export('request','42',baseline.baselineToken)
assert(exported.fullSize==true and exported.quality==100 and exported.format=='JPEG')
assert(rendered.LR_size_doConstrain==false and rendered.LR_jpeg_quality==1)
assert(rendered.LR_jpeg_useLimitSize==false and rendered.LR_export_colorSpace=='sRGB')
assert(rendered.LR_reimportExportedPhoto==false)
settings.Exposure2012=1
assert(not pcall(function() batch.export('stale','42',baseline.baselineToken) end))
baseline=batch.settings('bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','42')
mutateRender=true
assert(not pcall(function() batch.export('racing','42',baseline.baselineToken) end))
assert(deleted==true)
'''
        payload = (setup + '\n' + source + '\n' + assertions).encode()
        try:
            code = lib.luaL_loadbuffer(state, payload, len(payload), b'bridge_mock_test')
            if code == 0:
                code = lib.lua_pcall(state, 0, 0, 0)
            error = lib.lua_tolstring(state, -1, None) if code else None
            self.assertEqual(code, 0, error.decode() if error else '')
        finally:
            lib.lua_close(state)
