"""Actual Lua execution with mock SDK; no live catalog mutations."""
import copy
import ctypes
import unittest
from pathlib import Path
from test_main import h, proposal
from test_lua_bridge import LIB, BRIDGE


def run_lua(source):
    lib = ctypes.CDLL(str(LIB))
    lib.luaL_newstate.restype = ctypes.c_void_p
    lib.luaL_openlibs.argtypes = [ctypes.c_void_p]
    lib.luaL_loadbuffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]
    lib.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    lib.lua_tolstring.restype = ctypes.c_char_p
    lib.lua_close.argtypes = [ctypes.c_void_p]
    state = lib.luaL_newstate(); lib.luaL_openlibs(state)
    try:
        payload = source.encode()
        code = lib.luaL_loadbuffer(state, payload, len(payload), b'capability_test')
        if code == 0: code = lib.lua_pcall(state, 0, 0, 0)
        if code: raise AssertionError(lib.lua_tolstring(state, -1, None).decode())
    finally:
        lib.lua_close(state)


@unittest.skipUnless(LIB.exists(), 'Adobe Lua runtime absent')
class NativeBridgeTests(unittest.TestCase):
    def script(self, assertions):
        setup = r'''
local function clone(v) if type(v)~='table' then return v end local n={};for k,x in pairs(v) do n[k]=clone(x) end;return n end
local now,creates,snapshots,selectedId,failCreate,foreignEdit=0,0,0,nil,false,false
os.time=function() return now end
local settings={Exposure2012=0,GrainAmount=0,GrainSize=25,GrainFrequency=50,IncrementalTemperature=0,
    orientation='AB',CropAngle=0,ToneCurvePV2012={0,0,255,255},MaskGroupBasedCorrections={}}
local photo={}
function photo:getDevelopSettings() return clone(settings) end
function photo:checkPhotoAvailability() return true end
function photo:createDevelopSnapshot() snapshots=snapshots+1;return true end
function photo:applyDevelopSettings(patch) for k,v in pairs(patch) do settings[k]=clone(v) end end
local catalog={}
function catalog:getPath() return '/mock/catalog' end
function catalog:withWriteAccessDo(name,fn) fn();return 'executed' end
local D={}
function D.getRange(k)
    if k=='GrainAmount' or k=='GrainSize' or k=='GrainFrequency' then return 0,100 end
    if k=='Temperature' then return -100,100 end
    error('Unsupported control '..k)
end
function D.goToMasking() end
function D.getSelectedMask() return selectedId end
function D.createNewMask(kind,subtype)
    creates=creates+1
    assert(kind=='aiSelection' and subtype=='subject')
    if failCreate then return end
    selectedId='99999999-9999-9999-9999-999999999999'
    settings.MaskGroupBasedCorrections[#settings.MaskGroupBasedCorrections+1]={CorrectionID=selectedId,
        CorrectionName='Subject 1',CorrectionActive=true,LocalExposure2012=0,
        CorrectionMasks={{What='Mask/AI',MaskActive=true,MaskSyncID='opaque-native-data',MaskValue=1}}}
    if foreignEdit then settings.Exposure2012=9 end
end
local modules={LrTasks={sleep=function() now=now+1 end,pcall=pcall},LrDevelopController=D,
    LrApplicationView={switchToModule=function() end},LrApplication={},LrFileUtils={},LrPathUtils={},LrExportSession={}}
function import(k) assert(modules[k],k);return modules[k] end
package.preload.Config=function() return {root='/mock'} end
'''
        source = BRIDGE.read_text().split('local batch = NumericBatch.attach', 1)[0]
        setup += '\n' + source + r'''
local hooks={selected=function(id) assert(id=='42');return catalog,photo end,json=json,
    writeJson=function() end,path=function(p) return p end}
local api=NumericBatch.attach(hooks)
local token='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
local uid='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
local function baseline() return api.settings(token,'42') end
local function apply(rows) return api.apply('req','42',token..'\n'..rows..'\n',now+70) end
'''
        return setup + assertions

    def test_real_ranges_grain_wb_and_out_of_range_guard(self):
        run_lua(self.script(r'''
local b=baseline()
assert(b.numericRanges.GrainAmount[2]==100 and b.numericRanges.IncrementalTemperature[1]==-100)
assert(b.numericRanges.Temperature==nil and b.numericRanges.ColorGradeMidtoneHue==nil)
assert(b.features.numericRangeSources.GrainAmount=='LrDevelopController.getRange')
assert(not pcall(function() apply('GrainAmount=200') end) and snapshots==0)
local r=apply('GrainAmount=18\nGrainSize=22\nIncrementalTemperature=4')
assert(r.valuesMatch and settings.GrainAmount==18 and settings.WhiteBalance=='Custom')
assert(snapshots==1)
'''))

    def test_native_creation_preserves_generated_identity_and_reuses_it(self):
        run_lua(self.script(r'''
baseline()
local r=apply('mask:'..uid..':person:subject=\nlocal:'..uid..':ExposureEV=0.4')
assert(creates==1 and snapshots==1 and r.valuesMatch)
local g=settings.MaskGroupBasedCorrections[1]
assert(g.CorrectionID==selectedId and g.CorrectionName=='Codex AI subject person' and g.LocalExposure2012==0.1)
assert(g.CorrectionMasks[1].MaskSyncID=='opaque-native-data')
local real=selectedId:gsub('-',''):lower()
baseline()
apply('mask:'..real..':person:subject=\nlocal:'..real..':ExposureEV=0.8')
assert(creates==1 and settings.MaskGroupBasedCorrections[1].LocalExposure2012==0.2)
'''))

    def test_existing_people_mask_preserves_multi_component_selection(self):
        run_lua(self.script(r'''
local id='77777777-7777-7777-7777-777777777777'
settings.MaskGroupBasedCorrections={{CorrectionID=id,CorrectionName='人物皮肤',CorrectionActive=true,
    CorrectionMasks={{What='Mask/AI',MaskSyncID='skin'},{What='Mask/Range',MaskSyncID='intersection'}},LocalExposure2012=0}}
local old=json(settings.MaskGroupBasedCorrections[1].CorrectionMasks)
baseline();local key=id:gsub('-',''):lower()
local r=apply('mask:'..key..':existing-'..key..':existing=\nlocal:'..key..':Texture=-10')
assert(creates==0 and r.valuesMatch and settings.MaskGroupBasedCorrections[1].CorrectionName=='人物皮肤')
assert(json(settings.MaskGroupBasedCorrections[1].CorrectionMasks)==old)
assert(settings.MaskGroupBasedCorrections[1].LocalTexture==-0.1)
'''))

    def test_compact_and_mixed_case_mask_ids_preserve_identity_and_reuse(self):
        for mask_id in ('2494356EA8734A1F9CDD5F8AD2872D99',
                        '2494356e-a873-4A1F-9cdd-5f8ad2872d99'):
            with self.subTest(mask_id=mask_id):
                run_lua(self.script("local id='" + mask_id + "'\n" + r'''
settings.MaskGroupBasedCorrections={{CorrectionID=id,CorrectionName='蒙版 2',CorrectionActive=true,
    CorrectionMasks={{What='Mask/AI',MaskSyncID='skin'},{What='Mask/Range',MaskSyncID='intersection'}}}}
local key=id:gsub('-',''):lower()
local coverage=json(settings.MaskGroupBasedCorrections[1].CorrectionMasks)
baseline()
local r=apply('mask:'..key..':existing-'..key..':existing=\nlocal:'..key..':ExposureEV=0.65')
assert(r.valuesMatch and creates==0)
assert(settings.MaskGroupBasedCorrections[1].CorrectionID==id)
assert(settings.MaskGroupBasedCorrections[1].LocalExposure2012==0.65/4)
assert(json(settings.MaskGroupBasedCorrections[1].CorrectionMasks)==coverage)
settings.MaskGroupBasedCorrections[1].CorrectionName='Codex AI subject person'
baseline()
r=apply('mask:'..key..':person:subject=\nlocal:'..key..':ExposureEV=0.8')
assert(r.valuesMatch and creates==0 and #settings.MaskGroupBasedCorrections==1)
assert(settings.MaskGroupBasedCorrections[1].CorrectionID==id)
-- Equivalent IDs must never silently select one of two groups.
local other=clone(settings.MaskGroupBasedCorrections[1])
other.CorrectionID=key
settings.MaskGroupBasedCorrections[2]=other
baseline();local beforeSnapshots=snapshots
local ok,err=pcall(function() apply('mask:'..key..':person:subject=\nlocal:'..key..':ExposureEV=1') end)
assert(not ok and tostring(err):find('Duplicate existing mask ID'))
assert(snapshots==beforeSnapshots and creates==0)
'''))

    def test_native_timeout_consumes_token_and_never_replays(self):
        run_lua(self.script(r'''
baseline();failCreate=true
local request='mask:'..uid..':person:subject=\nlocal:'..uid..':ExposureEV=0.4'
assert(not pcall(function() apply(request) end))
assert(creates==1 and snapshots==1 and settings.Exposure2012==0)
assert(not pcall(function() apply(request) end))
assert(creates==1)
'''))

    def test_native_concurrent_edit_stops_before_adjustments(self):
        run_lua(self.script(r'''
baseline();foreignEdit=true
assert(not pcall(function() apply('mask:'..uid..':person:subject=\nlocal:'..uid..':ExposureEV=0.4') end))
assert(settings.MaskGroupBasedCorrections[1].LocalExposure2012==0)
'''))


class NativePlanTests(unittest.TestCase):
    def baseline(self):
        return {'settings': {'photoId':'42','baselineToken':'a'*32,'numericRanges':{'Exposure2012':[-5,5]},
            'features':{'aiMaskCreation':['subject','sky','background']},
            'develop':{'Exposure2012':0,'orientation':'AB','CropAngle':0,'MaskGroupBasedCorrections':[]}}}

    def test_ai_plan_requires_advertised_capability_and_no_fabricated_geometry(self):
        b=self.baseline();p=proposal()
        p['masks']=[{'id':'','name':'person','type':'subject','geometry':[],
                     'settings':[{'name':'ExposureEV','value':0.4}]}]
        caps={'applyNumeric':True,'applyStructured':True,'nativeMasks':True}
        wire=h.compile_plan(p,b,caps,{})
        self.assertEqual(wire['masks'][0]['geometry'],[])
        b['settings']['features']['aiMaskCreation']=[]
        with self.assertRaisesRegex(h.WorkflowError,'subtype unavailable'):h.compile_plan(p,b,caps,{})
        p['masks'][0]['geometry']=[0,0,1,1]
        with self.assertRaisesRegex(h.WorkflowError,'must not supply geometry'):h.compile_plan(p,b,caps,{})

    def test_native_readback_matches_real_id_and_rejects_changed_coverage(self):
        before=self.baseline()['settings']['develop'];uid='b'*32;actual_id='9'*32
        wire={'settings':{},'curves':{},'masks':[{'id':uid,'name':'person','type':'subject','geometry':[],
                                               'settings':{'ExposureEV':0.4}}]}
        group={'CorrectionID':actual_id,'CorrectionName':'Codex AI subject person','CorrectionActive':True,
               'CorrectionMasks':[{'What':'Mask/AI','MaskSyncID':'opaque'}],'LocalExposure2012':0.1}
        after=copy.deepcopy(before);after['MaskGroupBasedCorrections']=[group]
        response={'valuesMatch':True,'maskResults':{uid:{'verified':True,'actual':copy.deepcopy(group)}}}
        h.verify_readback(wire,before,after,response)
        group['CorrectionMasks'][0]['MaskSyncID']='different'
        with self.assertRaisesRegex(h.WorkflowError,'coverage changed'):h.verify_readback(wire,before,after,response)
