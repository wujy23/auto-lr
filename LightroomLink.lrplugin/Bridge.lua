-- SDK bridge: inspect, preview, and single-photo exposure with a snapshot. No network or eval.
local LrApplication = import 'LrApplication'
local LrTasks = import 'LrTasks'
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local LrExportSession = import 'LrExportSession'
local Config = require 'Config'
local DevelopCapabilities = (function()
-- Probe documented controller parameters on the selected photo, never invent ranges.
local M = {}
local aliases = {Exposure2012='Exposure', Contrast2012='Contrast', Highlights2012='Highlights',
    Shadows2012='Shadows', Whites2012='Whites', Blacks2012='Blacks', Clarity2012='Clarity',
    IncrementalTemperature='Temperature', IncrementalTint='Tint'}
local panels = {
    basic={'Exposure2012','Contrast2012','Highlights2012','Shadows2012','Whites2012','Blacks2012',
        'Temperature','Tint','IncrementalTemperature','IncrementalTint','Texture','Clarity2012','Dehaze','Vibrance','Saturation'},
    curve={'ParametricDarks','ParametricLights','ParametricShadows','ParametricHighlights',
        'ParametricShadowSplit','ParametricMidtoneSplit','ParametricHighlightSplit','CurveRefineSaturation'},
    grading={'SplitToningShadowHue','SplitToningShadowSaturation','ColorGradeShadowLum',
        'SplitToningHighlightHue','SplitToningHighlightSaturation','ColorGradeHighlightLum',
        'ColorGradeMidtoneHue','ColorGradeMidtoneSat','ColorGradeMidtoneLum','ColorGradeGlobalHue',
        'ColorGradeGlobalSat','ColorGradeGlobalLum','SplitToningBalance','ColorGradeBlending'},
    detail={'Sharpness','SharpenRadius','SharpenDetail','SharpenEdgeMasking','LuminanceSmoothing',
        'LuminanceNoiseReductionDetail','LuminanceNoiseReductionContrast','ColorNoiseReduction',
        'ColorNoiseReductionDetail','ColorNoiseReductionSmoothness'},
    effects={'PostCropVignetteAmount','PostCropVignetteMidpoint','PostCropVignetteFeather',
        'PostCropVignetteRoundness','PostCropVignetteHighlightContrast','GrainAmount','GrainSize','GrainFrequency'},
    calibration={'ShadowTint','RedHue','RedSaturation','GreenHue','GreenSaturation','BlueHue','BlueSaturation'},
    lens={'DefringePurpleAmount','DefringePurpleHueLo','DefringePurpleHueHi',
        'DefringeGreenAmount','DefringeGreenHueLo','DefringeGreenHueHi'},
    hsl={},
}
for _,c in ipairs({'Red','Orange','Yellow','Green','Aqua','Blue','Purple','Magenta'}) do
    for _,p in ipairs({'HueAdjustment','SaturationAdjustment','LuminanceAdjustment'}) do
        panels.hsl[#panels.hsl+1]=p..c
    end
end
local enables={curve='EnableToneCurve',grading='EnableSplitToning',detail='EnableDetail',
    effects='EnableEffects',calibration='EnableCalibration',lens='EnableLensCorrections',hsl='EnableColorAdjustments'}
function M.probe(settings, fallback)
    local ranges, sources = {}, {}
    -- Old hosts still retain only the previously supported, photo-present parameters.
    for key,limits in pairs(fallback) do
        if type(settings[key])=='number' then ranges[key]=limits; sources[key]='bridge-validated' end
    end
    local ok,D=pcall(import,'LrDevelopController')
    local okV,V=pcall(import,'LrApplicationView')
    local T=import 'LrTasks'
    if ok and okV then
        local switched=T.pcall(function() V.switchToModule('develop') end)
        if switched then
            for panel,keys in pairs(panels) do
                for _,key in ipairs(keys) do
                    if settings[enables[panel]]==false then ranges[key]=nil;sources[key]=nil
                    elseif type(settings[key])=='number' then
                        local success,lo,hi=T.pcall(function() return D.getRange(aliases[key] or key) end)
                        if success and type(lo)=='number' and type(hi)=='number' and lo==lo and hi==hi
                            and lo>-math.huge and hi<math.huge and lo<hi then
                            ranges[key]={lo,hi}; sources[key]='LrDevelopController.getRange'
                        end
                    end
                end
            end
        end
    end
    local native = ok and type(D.createNewMask)=='function' and type(D.getSelectedMask)=='function'
    return ranges, {version=2, numericRangeSources=sources,
        aiMaskCreation=native and {'subject','sky','background'} or {},
        existingMaskAdjustments=true,
        interactiveMaskTypes={'people','objects','landscape','brush','colorRange','luminanceRange'},
        limitations={'人物部位/指定人物、对象框选、风景分类和范围采样需先在LR完成；可读取并调整其现有蒙版。',
            '尚未自动化裁切、透视、配置文件切换、生成式移除、AI降噪、镜头虚化；不将它们声明为可执行。'}}
end
return M

end)()
local NativeMasks = (function()
-- Native AI computes the coverage. No synthetic AI fields or recycled bitmaps.
local M = {}
function M.create(photoId, kind, before, h, deadline)
    assert(kind=='subject' or kind=='sky' or kind=='background', 'AI selection requires interactive setup for this subtype.')
    local D=import 'LrDevelopController'
    local V=import 'LrApplicationView'
    local T=import 'LrTasks'
    V.switchToModule('develop'); D.goToMasking()
    local catalog,photo=h.selected(photoId)
    assert(h.json(photo:getDevelopSettings())==h.json(before),'Settings changed before AI selection.')
    local old={};for _,g in ipairs(before.MaskGroupBasedCorrections or {}) do old[g.CorrectionID]=true end
    D.createNewMask('aiSelection',kind)
    local previous,stable=nil,0
    while os.time()<deadline do
        T.sleep(0.25)
        local live,p=h.selected(photoId)
        assert(live:getPath()==catalog:getPath(),'Catalog changed during AI selection.')
        local after=p:getDevelopSettings()
        -- Native selection may only add its mask; reject unrelated edits.
        for k,v in pairs(before) do if k~='MaskGroupBasedCorrections' then
            assert(h.json(after[k])==h.json(v),'Unrelated setting changed during AI selection: '..k)
        end end
        local created={};local present={}
        for _,g in ipairs(after.MaskGroupBasedCorrections or {}) do
            if not old[g.CorrectionID] then created[#created+1]=g else present[g.CorrectionID]=g end
        end
        for _,g in ipairs(before.MaskGroupBasedCorrections or {}) do
            assert(h.json(present[g.CorrectionID])==h.json(g),'Existing mask changed during AI selection.')
        end
        assert(#created<=1,'AI selection created ambiguous masks; inspect Lightroom before retrying.')
        if #created==1 and created[1].CorrectionID and #(created[1].CorrectionMasks or {})>0 then
            local g=created[1]
            local fingerprint=h.json(g)
            if fingerprint==previous then stable=stable+1 else previous=fingerprint;stable=0 end
            -- A stable generated mask and matching native selection are necessary.
            if stable>=4 and D.getSelectedMask()==g.CorrectionID then return g,after end
        end
    end
    error('AI mask did not complete before timeout. It may still exist in Lightroom; do not automatically retry.')
end
return M

end)()
local M = { running = false }
local function path(s) return LrPathUtils.child(Config.root, s) end

local function quote(s)
    return '"' .. s:gsub('[%z\1-\31\\"]', function(c)
        if c == '"' then return '\\"' end
        if c == '\\' then return '\\\\' end
        return string.format('\\u%04x', string.byte(c))
    end) .. '"'
end
local function json(v)
    local t = type(v)
    if t == 'nil' then return 'null' end
    if t == 'boolean' then return tostring(v) end
    if t == 'number' then
        if v ~= v or v == math.huge or v == -math.huge then return 'null' end
        return string.format('%.17g', v)
    end
    if t == 'string' then return quote(v) end
    if t ~= 'table' then return quote(tostring(v)) end
    local keys, parts, array = {}, {}, true
    for k in pairs(v) do
        keys[#keys + 1] = k
        if type(k) ~= 'number' or k < 1 or k % 1 ~= 0 then array = false end
    end
    if array and #keys > 0 then
        for i=1,#keys do if v[i] == nil then array = false end end
        if array then
            for i=1,#keys do parts[i] = json(v[i]) end
            return '[' .. table.concat(parts, ',') .. ']'
        end
    end
    table.sort(keys, function(a,b) return tostring(a) < tostring(b) end)
    for _, k in ipairs(keys) do parts[#parts + 1] = quote(tostring(k)) .. ':' .. json(v[k]) end
    return '{' .. table.concat(parts, ',') .. '}'
end
local function writeJson(filename, value)
    local tmp = filename .. '.tmp'
    local f, e = io.open(tmp, 'wb')
    assert(f, e)
    local ok, err = f:write(json(value) .. '\n')
    f:close()
    assert(ok, err)
    -- Lightroom excludes os.rename; use its supported filesystem API.
    if LrFileUtils.exists(filename) then assert(LrFileUtils.delete(filename)) end
    assert(LrFileUtils.move(tmp, filename))
end
local function selected(expectedId)
    local catalog = LrApplication.activeCatalog()
    local catalogPath = catalog:getPath()
    local photo = catalog:getTargetPhoto()
    -- Import/source refresh can temporarily clear the target. Observe only:
    -- never reselect here, especially during guarded writes and readback.
    if not photo and expectedId and expectedId ~= '' then
        for _=1,20 do
            LrTasks.sleep(0.1)
            assert(LrApplication.activeCatalog():getPath() == catalogPath, 'Catalog changed.')
            photo = catalog:getTargetPhoto()
            if photo then break end
        end
    end
    assert(photo, 'No active photo after waiting for Lightroom. Select the requested photo and retry; check Library filters and collapsed stacks.')
    if expectedId and expectedId ~= '' then
        assert(tostring(photo.localIdentifier) == expectedId, 'Selected photo changed; request rejected.')
    end
    return catalog, photo
end
local function inspect(expectedId)
    local catalog, photo = selected(expectedId)
    local settings = photo:getDevelopSettings()
    local values, structures = {}, {}
    -- Return numeric/text settings but summarize potentially large AI/mask structures.
    for k,v in pairs(settings) do
        if type(v) == 'number' or type(v) == 'boolean' or type(v) == 'string' then
            values[k] = v
        elseif type(v) == 'table' then
            local count = 0
            for _ in pairs(v) do count = count + 1 end
            structures[k] = { entries = count }
        end
    end
    return {
        photoId = tostring(photo.localIdentifier),
        catalogPath = catalog:getPath(),
        filePath = photo:getRawMetadata('path'),
        filename = photo:getFormattedMetadata('fileName'),
        format = photo:getRawMetadata('fileFormat'),
        isVirtualCopy = photo:getRawMetadata('isVirtualCopy'),
        originalAvailable = photo:checkPhotoAvailability(),
        selectedCount = #catalog:getTargetPhotos(),
        develop = values,
        structures = structures,
    }
end
local function unhex(value)
    assert(#value % 2 == 0 and value:match('^[a-f0-9]+$'), 'Invalid encoded path.')
    local decoded = value:gsub('..', function(pair) return string.char(tonumber(pair,16)) end)
    assert(not decoded:find('%z'), 'NUL in path.')
    return decoded
end
local function preparePhoto(expectedId, encodedPath, encodedCatalog)
    local filename = unhex(encodedPath)
    assert(LrPathUtils.isAbsolute(filename) and LrFileUtils.exists(filename) == 'file', 'Source file missing.')
    local catalog = LrApplication.activeCatalog()
    local catalogPath = catalog:getPath()
    if encodedCatalog ~= '-' then assert(catalogPath == unhex(encodedCatalog), 'Catalog changed.') end
    local photo, imported = catalog:findPhotoByPath(filename), false
    if expectedId ~= '' then
        -- Preserve explicit virtual-copy selection; never silently choose its master.
        local active = catalog:getTargetPhoto()
        if active and tostring(active.localIdentifier) == expectedId and active:getRawMetadata('path') == filename then photo = active end
        assert(photo and tostring(photo.localIdentifier) == expectedId, 'Requested photo ID not found at path; select the intended virtual copy.')
    end
    if not photo then
        local result = catalog:withWriteAccessDo('Codex import photo in place', function()
            assert(LrApplication.activeCatalog():getPath() == catalogPath, 'Catalog changed.')
            photo = catalog:findPhotoByPath(filename)
            if not photo then photo = catalog:addPhoto(filename); imported = true end
        end, {timeout=10})
        assert(result == 'executed' and photo, 'Import did not complete.')
    end
    assert(LrApplication.activeCatalog():getPath() == catalogPath, 'Catalog changed.')
    -- Selection only works for photos visible in the current Library source.
    -- Importing a file does not automatically activate its folder.
    local folder = catalog:getFolderByPath(LrPathUtils.parent(filename))
    assert(folder, 'Imported photo folder is not available in the catalog.')
    assert(catalog:setActiveSources({folder}) ~= false, 'Could not activate the source photo folder.')
    local expected = tostring(photo.localIdentifier)
    local matched, stableTicks = false, 0
    -- Source switching and import settle asynchronously. Reapply selection only
    -- during prepare, never during guarded develop writes or readback.
    for _=1,100 do
        assert(LrApplication.activeCatalog():getPath() == catalogPath, 'Catalog changed.')
        local active = catalog:getTargetPhoto()
        if not active or tostring(active.localIdentifier) ~= expected then
            stableTicks = 0
            catalog:setSelectedPhotos(photo, {photo})
        end
        LrTasks.sleep(0.1)
        assert(LrApplication.activeCatalog():getPath() == catalogPath, 'Catalog changed.')
        active = catalog:getTargetPhoto()
        if active and tostring(active.localIdentifier) == expected and #catalog:getTargetPhotos() == 1 then
            stableTicks = stableTicks + 1
            if stableTicks >= 20 then matched = true; break end
        else
            stableTicks = 0
        end
    end
    assert(matched, 'Auto-selection failed. In Library, open the source photo folder, clear Library filters, expand any collapsed stack, and select the requested photo; then retry.')
    local result = inspect(expected)
    assert(result.filePath == filename and result.catalogPath == catalogPath, 'Selected source changed.')
    result.imported = imported
    return result
end
local function preview(id, expectedId)
    assert(expectedId and expectedId ~= '', 'Preview requires a photo ID from inspect.')
    local _, photo = selected(expectedId)
    local folder = path('previews/' .. id)
    LrFileUtils.createAllDirectories(folder)
    local session = LrExportSession {
        photosToExport = { photo },
        exportSettings = {
            LR_export_destinationType = 'specificFolder',
            LR_export_destinationPathPrefix = folder,
            LR_export_useSubfolder = false,
            LR_collisionHandling = 'skip',
            LR_format = 'JPEG', LR_jpeg_quality = 0.9,
            LR_export_colorSpace = 'sRGB',
            LR_size_doConstrain = true, LR_size_resizeType = 'longEdge',
            LR_size_maxHeight = 2000, LR_size_maxWidth = 2000,
            LR_size_doNotEnlarge = true,
            LR_outputSharpeningOn = false,
            LR_minimizeEmbeddedMetadata = true,
            LR_removeLocationMetadata = true,
            LR_removeFaceMetadata = true,
            LR_useWatermark = false,
            LR_reimportExportedPhoto = false,
            LR_renamingTokensOn = false,
        },
    }
    for _, rendition in session:renditions() do
        local ok, result = rendition:waitForRender()
        assert(ok, tostring(result))
        return { photoId = expectedId, previewPath = result, colorSpace = 'sRGB' }
    end
    error('Lightroom returned no rendition.')
end
local function setExposure(id, expectedId, value, expectedBefore)
    assert(expectedId ~= '', 'Exposure requires a photo ID.')
    value, expectedBefore = tonumber(value), tonumber(expectedBefore)
    assert(value and value >= -5 and value <= 5, 'Exposure must be between -5 and 5.')
    assert(expectedBefore, 'Expected previous exposure required.')
    local catalog, photo = selected(expectedId)
    assert(photo:checkPhotoAvailability(), 'Original photo unavailable.')
    local before = photo:getDevelopSettings()
    assert(before.Exposure2012 == expectedBefore, 'Exposure changed; inspect again.')
    writeJson(path('backups/' .. id .. '.json'), {
        photoId = expectedId, catalogPath = catalog:getPath(), develop = before,
    })
    local snapshot = 'Before exposure test - ' .. id:sub(1, 8)
    local result = catalog:withWriteAccessDo('Codex exposure test', function()
        selected(expectedId)
        assert(photo:getDevelopSettings().Exposure2012 == expectedBefore, 'Exposure changed; request rejected.')
        assert(photo:createDevelopSnapshot(snapshot, false), 'Snapshot failed.')
        photo:applyDevelopSettings({ Exposure2012 = value }, 'Codex exposure ' .. tostring(value))
    end, { timeout = 5 })
    assert(result == 'executed', 'Catalog write was not executed.')
    local after = photo:getDevelopSettings().Exposure2012
    return { photoId = expectedId, before = before.Exposure2012, requested = value,
        after = after, verified = after == value, snapshot = snapshot }
end
local function maskData(expectedId)
    local _, photo = selected(expectedId)
    local s = photo:getDevelopSettings()
    return { photoId = tostring(photo.localIdentifier), globalExposure = s.Exposure2012,
        groups = s.MaskGroupBasedCorrections or {} }
end
local function maskProbe(expectedId)
    local _, photo = selected(expectedId)
    local D = import 'LrDevelopController'
    D.goToMasking()
    for _, g in ipairs(photo:getDevelopSettings().MaskGroupBasedCorrections or {}) do
        if g.CorrectionName == 'Codex center radial EV test' then
            D.selectMask(g.CorrectionID)
        end
    end
    LrTasks.sleep(0.3)
    local lo, hi = D.getRange('local_Exposure')
    return { minExposure = lo, maxExposure = hi, currentExposure = D.getValue('local_Exposure'),
        selectedMask = D.getSelectedMask(), masks = D.getAllMasks() }
end
local function tuneRadial(id, expectedId, ev, expectedCount)
    assert(expectedId ~= '', 'Photo ID required.')
    ev, expectedCount = tonumber(ev), tonumber(expectedCount)
    assert(ev and ev >= -5 and ev <= 5, 'EV outside test range.')
    local catalog, photo = selected(expectedId)
    local before = photo:getDevelopSettings()
    local fingerprint = json(before)
    local groups = before.MaskGroupBasedCorrections or {}
    assert(#groups == expectedCount, 'Mask count changed.')
    local target
    for _, g in ipairs(groups) do
        if g.CorrectionName == 'Codex center radial EV test' then
            assert(not target, 'Multiple test masks; refusing ambiguous update.')
            target = g
        end
    end
    assert(target, 'Test mask not found.')
    writeJson(path('backups/' .. id .. '.json'), { photoId = expectedId, develop = before })
    target.LocalExposure2012 = ev/4
    target.CorrectionMasks[1].Flipped = true
    local result = catalog:withWriteAccessDo('Codex radial exposure', function()
        selected(expectedId)
        assert(json(photo:getDevelopSettings()) == fingerprint, 'Settings changed.')
        photo:applyDevelopSettings({MaskGroupBasedCorrections = groups}, 'Codex radial exposure '..ev)
    end, {timeout=5})
    assert(result == 'executed', 'Write was not executed.')
    LrTasks.sleep(0.5)
    return maskData(expectedId)
end
local function addRadial(id, expectedId, ev, expectedCount)
    assert(expectedId ~= '', 'Radial mask requires a photo ID.')
    ev, expectedCount = tonumber(ev), tonumber(expectedCount)
    assert(ev and ev >= -5 and ev <= 5, 'Test EV must be between -5 and 5.')
    assert(expectedCount and expectedCount >= 0, 'Expected mask count required.')
    local catalog, photo = selected(expectedId)
    local before = photo:getDevelopSettings()
    local groups = before.MaskGroupBasedCorrections or {}
    assert(#groups == expectedCount, 'Mask count changed; inspect again.')
    assert(before.orientation == 'AB' and (before.CropAngle or 0) == 0,
        'This prototype requires unrotated orientation AB.')
    assert(before.EnableMaskGroupBasedCorrections ~= false, 'Mask panel is disabled.')
    local fingerprint = json(before)
    writeJson(path('backups/' .. id .. '.json'), {
        photoId = expectedId, catalogPath = catalog:getPath(), develop = before,
    })
    local function uuid(hex)
        return (hex:sub(1,8)..'-'..hex:sub(9,12)..'-'..hex:sub(13,16)..'-'..hex:sub(17,20)..'-'..hex:sub(21,32)):upper()
    end
    local groupId, maskId = uuid(id), uuid(id:reverse())
    local left, right = before.CropLeft or 0, before.CropRight or 1
    local top, bottom = before.CropTop or 0, before.CropBottom or 1
    local cx, cy = (left + right)/2, (top + bottom)/2
    local rx, ry = (right-left)*0.20, (bottom-top)*0.25
    local mask = {
        What = 'Mask/CircularGradient', MaskName = 'Center radial',
        MaskID = maskId, MaskSyncID = id:reverse():upper(),
        MaskActive = true, MaskValue = 1, MaskBlendMode = 0, MaskInverted = false,
        Top = cy-ry, Bottom = cy+ry, Left = cx-rx, Right = cx+rx,
        Angle = 0, Feather = 50, Midpoint = 50, Roundness = 0,
        Flipped = false, Version = 2,
    }
    local group = {
        What = 'Correction', CorrectionID = groupId, CorrectionSyncID = id:upper(),
        CorrectionName = 'Codex center radial EV test', CorrectionActive = true,
        CorrectionAmount = 1, CorrectionReferenceX = cx, CorrectionReferenceY = cy,
        CorrectionMasks = { mask }, MainCurve = {'0,0','255,255'},
    }
    -- Local exposure uses a normalized scale; test EV/4 and verify LR's result.
    -- Neutralize every local scalar seen in this photo; preserve the original groups.
    for _, old in ipairs(groups) do
        for k,v in pairs(old) do
            if k:match('^Local') and type(v) == 'number' then group[k] = 0 end
        end
    end
    group.LocalCurveRefineSaturation = 100
    group.LocalExposure = 0
    group.LocalExposure2012 = ev / 4
    local oldGroups = json(groups)
    local snapshot = 'Before radial test - ' .. id:sub(1,8)
    local result = catalog:withWriteAccessDo('Codex center radial test', function()
        selected(expectedId)
        assert(json(photo:getDevelopSettings()) == fingerprint, 'Settings changed; request rejected.')
        assert(photo:createDevelopSnapshot(snapshot, false), 'Snapshot failed.')
        groups[#groups+1] = group
        photo:applyDevelopSettings({ MaskGroupBasedCorrections = groups }, 'Codex center radial')
    end, { timeout = 5 })
    assert(result == 'executed', 'Catalog write was not executed.')
    local after = photo:getDevelopSettings()
    local found, preserved = nil, {}
    for _, g in ipairs(after.MaskGroupBasedCorrections or {}) do
        if g.CorrectionID == groupId or g.CorrectionSyncID == id:upper() then found = g
        else preserved[#preserved+1] = g end
    end
    writeJson(path('responses/' .. id .. '-settings.json'), after)
    return { photoId = expectedId, requestedEV = ev, snapshot = snapshot,
        group = found, countBefore = expectedCount, countAfter = #(after.MaskGroupBasedCorrections or {}),
        localExposureRaw = found and found.LocalExposure2012,
        exposureEVByScale = found and found.LocalExposure2012*4,
        existingGroupsUnchanged = oldGroups == json(preserved),
        globalExposureUnchanged = after.Exposure2012 == before.Exposure2012 }
end
-- Guarded numeric develop settings. Input is data, never executable Lua.
local NumericBatch = {}
local ranges = {
    Exposure2012 = {-5,5}, Contrast2012 = {-100,100},
    Highlights2012 = {-100,100}, Shadows2012 = {-100,100},
    Whites2012 = {-100,100}, Blacks2012 = {-100,100},
    Saturation = {-100,100}, Vibrance = {-100,100},
    Texture = {-100,100}, Clarity2012 = {-100,100}, Dehaze = {-100,100},
    SplitToningShadowHue = {0,360}, SplitToningShadowSaturation = {0,100},
    SplitToningHighlightHue = {0,360}, SplitToningHighlightSaturation = {0,100},
    SplitToningBalance = {-100,100}, ColorGradeBlending = {0,100},
}
for _, color in ipairs({'Red','Orange','Yellow','Green','Aqua','Blue','Purple','Magenta'}) do
    for _, prefix in ipairs({'HueAdjustment','SaturationAdjustment','LuminanceAdjustment'}) do
        ranges[prefix .. color] = {-100,100}
    end
end
function NumericBatch.attach(h)
    local baselines = {}
    local api = {}
    local curveNames = {ToneCurvePV2012=true,ToneCurvePV2012Red=true,ToneCurvePV2012Green=true,ToneCurvePV2012Blue=true}
    local localFields = {
        ExposureEV={'LocalExposure2012',4,-4,4}, Contrast={'LocalContrast2012',100,-100,100},
        Highlights={'LocalHighlights2012',100,-100,100}, Shadows={'LocalShadows2012',100,-100,100},
        Whites={'LocalWhites2012',100,-100,100}, Blacks={'LocalBlacks2012',100,-100,100},
        Temperature={'LocalTemperature',100,-100,100}, Tint={'LocalTint',100,-100,100},
        Sharpness={'LocalSharpness',100,-100,100}, LuminanceNoise={'LocalLuminanceNoise',100,-100,100},
        Moire={'LocalMoire',100,-100,100}, Defringe={'LocalDefringe',100,-100,100},
        Saturation={'LocalSaturation',100,-100,100}, Texture={'LocalTexture',100,-100,100},
        Clarity={'LocalClarity2012',100,-100,100}, Dehaze={'LocalDehaze',100,-100,100},
    }
    local function copy(v)
        if type(v) ~= 'table' then return v end
        local out={}; for k,x in pairs(v) do out[k]=copy(x) end; return out
    end
    local function uuid(hex)
        return (hex:sub(1,8)..'-'..hex:sub(9,12)..'-'..hex:sub(13,16)..'-'..hex:sub(17,20)..'-'..hex:sub(21,32)):upper()
    end
    local function maskKey(id)
        if type(id) ~= 'string' then return nil end
        local key = id:gsub('-', ''):lower()
        if #key ~= 32 or not key:match('^[a-f0-9]+$') then return nil end
        return key
    end
    local function findMask(groups, id)
        local key = assert(maskKey(id), 'Invalid mask ID.')
        local found
        for _,group in ipairs(groups or {}) do
            if maskKey(group.CorrectionID) == key then
                assert(not found, 'Duplicate existing mask ID.')
                found = group
            end
        end
        return found
    end
    local function subset(expected, actual)
        if type(expected)=='number' then return type(actual)=='number' and math.abs(expected-actual)<0.00001 end
        if type(expected)~='table' then return expected==actual end
        if type(actual)~='table' then return false end
        for k,v in pairs(expected) do if not subset(v,actual[k]) then return false end end
        return true
    end
    function api.settings(id, photoId)
        assert(photoId ~= '', 'Photo ID required.')
        local catalog, photo = h.selected(photoId)
        local settings = photo:getDevelopSettings()
        local photoRanges, features = DevelopCapabilities.probe(settings, ranges)
        settings = photo:getDevelopSettings() -- module activation may normalize settings
        baselines[id] = { photoId = photoId, catalogPath = catalog:getPath(),
            fingerprint = h.json(settings), ranges=photoRanges }
        return { photoId = photoId, catalogPath = catalog:getPath(),
            baselineToken = id, develop = settings, numericRanges = photoRanges, features=features }
    end
    function api.export(id, photoId, token)
        local baseline = baselines[token]
        assert(baseline and baseline.photoId == photoId, 'Read fresh settings before export.')
        local catalog, photo = h.selected(photoId)
        assert(catalog:getPath() == baseline.catalogPath, 'Catalog changed.')
        assert(photo:checkPhotoAvailability(), 'Original photo unavailable.')
        assert(h.json(photo:getDevelopSettings()) == baseline.fingerprint, 'Settings changed before export.')
        local folder = h.path('exports/' .. id)
        LrFileUtils.createAllDirectories(folder)
        local session = LrExportSession {
            photosToExport = {photo},
            exportSettings = {
                LR_export_destinationType='specificFolder', LR_export_destinationPathPrefix=folder,
                LR_export_useSubfolder=false, LR_collisionHandling='skip',
                LR_format='JPEG', LR_jpeg_quality=1, LR_export_colorSpace='sRGB',
                LR_size_doConstrain=false, LR_size_doNotEnlarge=true,
                LR_jpeg_useLimitSize=false, LR_outputSharpeningOn=false,
                LR_minimizeEmbeddedMetadata=true, LR_removeLocationMetadata=true,
                LR_removeFaceMetadata=true, LR_useWatermark=false,
                LR_reimportExportedPhoto=false, LR_renamingTokensOn=false,
            },
        }
        for _, rendition in session:renditions() do
            local ok, filename = rendition:waitForRender()
            assert(ok, tostring(filename))
            local liveCatalog = h.selected(photoId)
            local matches = liveCatalog:getPath() == baseline.catalogPath
                and h.json(photo:getDevelopSettings()) == baseline.fingerprint
            if not matches then
                if LrFileUtils.exists(filename) then LrFileUtils.delete(filename) end
                error('Settings or catalog changed during export; result discarded.')
            end
            return {photoId=photoId, catalogPath=baseline.catalogPath, exportPath=filename,
                fullSize=true, format='JPEG', quality=100, colorSpace='sRGB', baselineToken=token}
        end
        error('Lightroom returned no export rendition.')
    end
    function api.apply(id, photoId, payload, deadline)
        local token, rows = payload:match('^([a-f0-9]+)\n(.*)$')
        assert(token and #token == 32, 'Invalid baseline token.')
        local baseline = baselines[token]
        assert(baseline and baseline.photoId == photoId, 'Read fresh settings before apply.')
        local catalog, photo = h.selected(photoId)
        assert(catalog:getPath() == baseline.catalogPath, 'Catalog changed.')
        assert(photo:checkPhotoAvailability(), 'Original photo unavailable.')
        local before = photo:getDevelopSettings()
        assert(h.json(before) == baseline.fingerprint, 'Settings changed since baseline.')
        local original=copy(before)
        local patch, count = {}, 0
        local maskSpecs, maskOrder = {}, {}
        for row in rows:gmatch('[^\n]+') do
          if row:sub(1,6)=='curve:' then
            local key, data=row:match('^curve:([A-Za-z0-9]+)=([%d%.eE%+%-%:,]+)$')
            assert(key and curveNames[key] and patch[key]==nil, 'Invalid or duplicate curve.')
            assert(before.EnableToneCurve~=false and type(before[key])=='table', 'Curve unavailable.')
            local points, previousX, previousY={},-1,-1
            for pair in data:gmatch('[^,]+') do
                local x,y=pair:match('^([%d%.eE%+%-]+):([%d%.eE%+%-]+)$');x,y=tonumber(x),tonumber(y)
                assert(x and y and x>previousX and x<=255 and y>=previousY and y>=0 and y<=255 and x>=0,'Invalid curve point.')
                points[#points+1]=x;points[#points+1]=y;previousX,previousY=x,y
            end
            assert(#points>=4 and #points<=32,'Invalid curve length.')
            patch[key]=points;patch.ToneCurveName2012='Custom';count=count+1
          elseif row:sub(1,5)=='mask:' then
            local uid,name,kind,data=row:match('^mask:([a-f0-9]+):([a-z0-9%-]+):([a-z]+)=([%d%.eE%+%-,]*)$')
            assert(uid and #uid==32 and #name<=64 and not maskSpecs[uid], 'Invalid or duplicate mask.')
            if kind=='linear' or kind=='radial' then
                assert(before.orientation=='AB' and (before.CropAngle or 0)==0,'Geometry requires unrotated AB orientation.')
            end
            assert(before.EnableMaskGroupBasedCorrections~=false,'Masks disabled.')
            local g={};for number in data:gmatch('[^,]+') do local v=tonumber(number);assert(v and v==v and math.abs(v)<1000,'Invalid geometry.');g[#g+1]=v end
            local native=kind=='subject' or kind=='sky' or kind=='background' or kind=='existing'
            assert((kind=='radial' and #g==6) or (kind=='linear' and #g==4) or (native and #g==0),'Unsupported geometry.')
            if kind=='radial' then assert(g[1]<g[3] and g[2]<g[4] and g[5]>=0 and g[5]<=100 and (g[6]==0 or g[6]==1),'Invalid ellipse.')
            elseif kind=='linear' then assert(g[1]~=g[3] or g[2]~=g[4],'Gradient has zero length.') end
            if not native then for i=1,4 do assert(g[i]>=-2 and g[i]<=3,'Geometry outside supported bounds.') end end
            maskSpecs[uid]={name=name,kind=kind,geometry=g,settings={}};maskOrder[#maskOrder+1]=uid;count=count+1
          elseif row:sub(1,6)=='local:' then
            local uid,key,raw=row:match('^local:([a-f0-9]+):([A-Za-z0-9]+)=([%d%.eE%+%-]+)$')
            local spec=uid and maskSpecs[uid];local rule=key and localFields[key];local value=tonumber(raw)
            assert(spec and rule and value and value==value and value>=rule[3] and value<=rule[4],'Invalid local parameter.')
            assert(spec.settings[rule[1]]==nil,'Duplicate local parameter.')
            spec.settings[rule[1]]=value/rule[2]
          else
            local key, raw = row:match('^([A-Za-z][A-Za-z0-9]*)=([%d%.eE%+%-]+)$')
            local range = key and baseline.ranges[key]
            local value = raw and tonumber(raw)
            assert(range and value and value == value and value >= range[1] and value <= range[2],
                'Unsupported field or out-of-range value: ' .. row)
            assert(patch[key] == nil, 'Duplicate field: ' .. key)
            assert(type(before[key]) == 'number', 'Field unavailable on this photo: ' .. key)
            patch[key], count = value, count + 1
          end
        end
        assert(count > 0 and count <= 140 and #maskOrder<=8, 'Invalid parameter count.')
        if patch.Temperature or patch.Tint or patch.IncrementalTemperature or patch.IncrementalTint then patch.WhiteBalance='Custom' end
        local split={patch.ParametricShadowSplit or before.ParametricShadowSplit,
            patch.ParametricMidtoneSplit or before.ParametricMidtoneSplit,patch.ParametricHighlightSplit or before.ParametricHighlightSplit}
        if patch.ParametricShadowSplit or patch.ParametricMidtoneSplit or patch.ParametricHighlightSplit then
            assert(split[1] and split[2] and split[3] and split[1]<split[2] and split[2]<split[3],'Parametric curve splits must remain ordered.')
        end
        local snapshot = 'Before Codex style - ' .. id:sub(1,8)
        local snapshotted=false
        local function snapshotOnce()
            if snapshotted then return end
            h.writeJson(h.path('backups/' .. id .. '.json'), {photoId=photoId,catalogPath=catalog:getPath(),develop=original})
            local result=catalog:withWriteAccessDo('Codex style snapshot',function()
                local live=h.selected(photoId);assert(live:getPath()==baseline.catalogPath,'Catalog changed.')
                assert(h.json(photo:getDevelopSettings())==baseline.fingerprint,'Settings changed before snapshot.')
                assert(photo:createDevelopSnapshot(snapshot,false),'Snapshot failed.')
            end,{timeout=5})
            assert(result=='executed','Snapshot write not executed.');snapshotted=true
        end
        local nativeIds={}
        local nativeBudget=os.time()+60
        for _,uid in ipairs(maskOrder) do
            local spec=maskSpecs[uid]
            if spec.kind=='subject' or spec.kind=='sky' or spec.kind=='background' then
                local existing = findMask(before.MaskGroupBasedCorrections, uid)
                if not existing then
                    snapshotOnce()
                    -- Invalidate immediately: native AI is already a mutation even if it fails later.
                    baselines[token]=nil
                    local created,after=NativeMasks.create(photoId,spec.kind,before,h,math.min(deadline or nativeBudget,nativeBudget))
                    nativeIds[uid]=created.CorrectionID;before=after
                end
            end
        end
        local maskChecks={}
        if #maskOrder>0 then
            local groups=copy(before.MaskGroupBasedCorrections or {})
            for _,uid in ipairs(maskOrder) do
                local spec=maskSpecs[uid];local g=spec.geometry;local group,shape
                group = findMask(groups, nativeIds[uid] or uid)
                if group then
                    if spec.kind=='linear' or spec.kind=='radial' then
                        assert(group.CorrectionName=='Codex '..spec.name and #group.CorrectionMasks==1,'Existing mask identity mismatch.')
                    elseif spec.kind~='existing' then
                        assert(nativeIds[uid] or group.CorrectionName=='Codex AI '..spec.kind..' '..spec.name,'Existing AI mask identity mismatch.')
                        group.CorrectionName='Codex AI '..spec.kind..' '..spec.name
                    end
                    assert(group.CorrectionActive~=false and #(group.CorrectionMasks or {})>0,'Inactive/empty mask.')
                    shape=group.CorrectionMasks[1]
                else
                    assert(spec.kind=='linear' or spec.kind=='radial','Native/existing mask was not found.')
                    shape={MaskID=uuid(uid:reverse()),MaskSyncID=uid:reverse():upper(),MaskName=spec.name,
                        MaskActive=true,MaskValue=1,MaskBlendMode=0,MaskInverted=false}
                    group={What='Correction',CorrectionID=uuid(uid),CorrectionSyncID=uid:upper(),
                        CorrectionName='Codex '..spec.name,CorrectionActive=true,CorrectionAmount=1,
                        CorrectionMasks={shape},MainCurve={'0,0','255,255'},LocalCurveRefineSaturation=100,
                        LocalExposure=0,LocalBrightness=0,LocalContrast=0,LocalClarity=0,LocalSharpness=0,
                        LocalLuminanceNoise=0,LocalMoire=0,LocalDefringe=0,LocalHue=0,
                        LocalToningHue=0,LocalToningSaturation=0,LocalGrain=0}
                    for _,rule in pairs(localFields) do group[rule[1]]=0 end
                    groups[#groups+1]=group
                end
                if spec.kind=='radial' then
                    assert(not shape.What or shape.What=='Mask/CircularGradient','Mask type mismatch.')
                    shape.What='Mask/CircularGradient';shape.Left=g[1];shape.Top=g[2];shape.Right=g[3];shape.Bottom=g[4]
                    shape.Angle=0;shape.Feather=g[5];shape.Flipped=g[6]==1;shape.Midpoint=50;shape.Roundness=0;shape.Version=2
                    group.CorrectionReferenceX=(g[1]+g[3])/2;group.CorrectionReferenceY=(g[2]+g[4])/2
                elseif spec.kind=='linear' then
                    assert(not shape.What or shape.What=='Mask/Gradient','Mask type mismatch.')
                    shape.What='Mask/Gradient';shape.FullX=g[1];shape.FullY=g[2];shape.ZeroX=g[3];shape.ZeroY=g[4]
                    group.CorrectionReferenceX=g[1];group.CorrectionReferenceY=g[2]
                end
                local n=0;for k,v in pairs(spec.settings) do group[k]=v;n=n+1 end;assert(n>0,'Empty local adjustments.')
                maskChecks[uid]={CorrectionID=group.CorrectionID,CorrectionName=group.CorrectionName,
                    CorrectionMasks=copy(group.CorrectionMasks)}
                for k,v in pairs(spec.settings) do maskChecks[uid][k]=v end
            end
            patch.MaskGroupBasedCorrections=groups
        end
        -- Disabled panels must not silently swallow adjustments.
        assert(before.EnableColorAdjustments ~= false, 'Color adjustments disabled.')
        assert(before.EnableSplitToning ~= false, 'Color grading disabled.')
        snapshotOnce()
        local writeFingerprint=h.json(before)
        local result = catalog:withWriteAccessDo('Codex style edit', function()
            local liveCatalog = h.selected(photoId)
            assert(liveCatalog:getPath() == baseline.catalogPath, 'Catalog changed.')
            assert(h.json(photo:getDevelopSettings()) == writeFingerprint, 'Settings changed before write.')
            photo:applyDevelopSettings(patch, 'Codex style edit')
        end, {timeout = 5})
        assert(result == 'executed', 'Catalog write not executed.')
        baselines[token] = nil
        LrTasks.sleep(0.5)
        local after = photo:getDevelopSettings()
        local actual, matches = {}, true
        for key, value in pairs(patch) do
            actual[key] = after[key]
            if key~='MaskGroupBasedCorrections' and not subset(value,after[key]) then matches = false end
        end
        local maskResults={}
        for uid,expected in pairs(maskChecks) do
            local found;for _,group in ipairs(after.MaskGroupBasedCorrections or {}) do if group.CorrectionID==expected.CorrectionID then found=group end end
            local ok=subset(expected,found);maskResults[uid]={verified=ok,actual=found};if not ok then matches=false end
        end
        h.writeJson(h.path('responses/' .. id .. '-settings.json'), after)
        return { photoId = photoId, snapshot = snapshot, requested = patch, actual = actual,
            valuesMatch = matches, baselineToken = token, maskResults=maskResults,
            masksUnchanged = h.json(before.MaskGroupBasedCorrections) == h.json(after.MaskGroupBasedCorrections),
            curvesUnchanged = h.json(before.ToneCurvePV2012) == h.json(after.ToneCurvePV2012)
                and h.json(before.ToneCurvePV2012Red) == h.json(after.ToneCurvePV2012Red)
                and h.json(before.ToneCurvePV2012Green) == h.json(after.ToneCurvePV2012Green)
                and h.json(before.ToneCurvePV2012Blue) == h.json(after.ToneCurvePV2012Blue) }
    end
    return api
end

local batch = NumericBatch.attach { selected = selected, json = json, writeJson = writeJson, path = path }
-- Experimental SDK-only probe. Restricted to a named disposable file.
local function peopleProbe(id, expectedId, mode)
    local catalog, photo = selected(expectedId)
    local source = photo:getRawMetadata('path')
    local allowed = LrPathUtils.child(LrPathUtils.parent(_PLUGIN.path), 'work/people-probe/DSC_0402-sdk-probe.NEF')
    assert(source == allowed, 'People probe only accepts its isolated DSC_0402 copy.')
    local cases = {peopleskinall={3,'0.500000 0.500000'},
        peopleskinleft={0,'0.375000 0.340000'}, peopleskinright={0,'0.550000 0.320000'}, peoplebuiltin={3,'0.500000 0.500000'},
        peopledirectleft={0,'0.375000 0.340000'}, peopledirectright={0,'0.550000 0.320000'},
        peoplegrouprefs={0,'0.550000 0.320000'}, peoplepointarray={0,'0.550000 0.320000'},
        peoplepointmap={0,'0.550000 0.320000'}, peopletwogroups={0,'0.375000 0.340000'},
        peoplecandidates={0,'0.500000 0.500000'}}
    local case = assert(cases[mode], 'Unknown people probe mode.')
    local before = photo:getDevelopSettings()
    writeJson(path('backups/' .. id .. '-people.json'), {photoId=expectedId, mode=mode, develop=before})
    local function guard()
        local c,p=selected(expectedId)
        assert(c:getPath()==catalog:getPath() and p:getRawMetadata('path')==allowed, 'Probe target changed.')
    end
    if mode=='peoplecandidates' then
        local D=import 'LrDevelopController'
        local V=import 'LrApplicationView'
        guard();V.switchToModule('develop');D.goToMasking()
        local beforeMasks=D.getAllMasks()
        local methods={}
        for k,v in pairs(D) do
            if type(v)=='function' and (k:lower():find('mask') or k:lower():find('person') or k:lower():find('people')) then methods[#methods+1]=k end
        end
        table.sort(methods)
        D.createNewMask('aiSelection','people')
        LrTasks.sleep(4);guard()
        return {photoId=expectedId,mode=mode,beforeMasks=beforeMasks,afterMasks=D.getAllMasks(),
            selectedMask=D.getSelectedMask(),publicMaskMethods=methods,
            groups=photo:getDevelopSettings().MaskGroupBasedCorrections or {}}
    end
    local presetSettings = {MaskGroupBasedCorrections={{What='Correction',
        CorrectionName='Codex SDK probe '..mode, CorrectionActive=true, CorrectionAmount=1,
        CorrectionSyncID=id:upper(), LocalExposure2012=0.25, LocalContrast2012=0,
        CorrectionMasks={{What='Mask/Image', MaskActive=true, MaskName='Probe Facial Skin',
            MaskBlendMode=0, MaskInverted=false, MaskSyncID=id:reverse():upper(), MaskValue=1,
            MaskVersion=1, MaskSubType=case[1], MaskSubCategoryID=2,
            ReferencePoint=case[2], ErrorReason=0}}}}}
    local group=presetSettings.MaskGroupBasedCorrections[1]
    local mask=group.CorrectionMasks[1]
    if mode=='peoplegrouprefs' then
        group.CorrectionReferenceX=0.55;group.CorrectionReferenceY=0.32
    elseif mode=='peoplepointarray' then
        mask.ReferencePoint={0.55,0.32}
    elseif mode=='peoplepointmap' then
        mask.ReferencePoint={x=0.55,y=0.32}
    elseif mode=='peopletwogroups' then
        group.CorrectionReferenceX=0.375;group.CorrectionReferenceY=0.34
        local function clone(v)
            if type(v)~='table' then return v end
            local out={};for k,x in pairs(v) do out[k]=clone(x) end;return out
        end
        local second=clone(group)
        second.CorrectionName='Codex SDK probe second candidate'
        second.CorrectionSyncID=id:reverse():upper()
        second.CorrectionMasks[1].MaskSyncID=id:upper()
        second.CorrectionMasks[1].ReferencePoint='0.550000 0.320000'
        second.CorrectionReferenceX=0.55;second.CorrectionReferenceY=0.32
        presetSettings.MaskGroupBasedCorrections[2]=second
    end
    -- Subtype 3 is taken from Adobe's installed Smooth Face preset.
    -- Subtype 0 + a reference point is a hypothesis based on a real person mask,
    -- not an advertised production capability. No source-image cached mask is copied.
    local builtin = mode=='peoplebuiltin'
    local direct = mode=='peopledirectleft' or mode=='peopledirectright' or mode=='peoplegrouprefs' or mode=='peoplepointarray' or mode=='peoplepointmap' or mode=='peopletwogroups'
    local preset
    if builtin then preset=LrApplication.developPresetByUuid('798869B7B7234F0EBF221B90067B07FF')
    elseif direct then preset=true
    else preset=LrApplication.addDevelopPresetForPlugin(_PLUGIN, 'Codex SDK people probe '..mode, presetSettings) end
    assert(preset, 'Could not create isolated probe preset.')
    local result=catalog:withWriteAccessDo('Codex isolated people probe', function()
        guard()
        assert(json(photo:getDevelopSettings())==json(before), 'Probe settings changed before write.')
        assert(photo:createDevelopSnapshot('Before SDK people probe '..id:sub(1,8),false), 'Snapshot failed.')
        -- Previous experiment groups are removed only on this disposable copy.
        photo:applyDevelopSettings({MaskGroupBasedCorrections={}}, 'Reset isolated probe masks')
    end,{timeout=5})
    assert(result=='executed','Probe reset failed.')
    LrTasks.sleep(0.3)
    result=catalog:withWriteAccessDo('Recompute AI people probe', function()
        guard()
        if direct then photo:applyDevelopSettings(presetSettings,'Probe point-specific mask')
        elseif builtin then photo:applyDevelopPreset(preset,nil,100,true)
        else photo:applyDevelopPreset(preset,_PLUGIN,100,true) end
    end,{timeout=5})
    assert(result=='executed','Probe preset application failed.')
    LrTasks.sleep(0.5)
    writeJson(path('responses/' .. id .. '-before-recompute.json'),photo:getDevelopSettings())
    assert(type(catalog.updateAISettings)=='function', 'SDK lacks catalog:updateAISettings.')
    result=catalog:withWriteAccessDo('Calculate isolated people masks',function()
        guard();catalog:updateAISettings({photo})
    end,{timeout=5})
    assert(result=='executed','AI recompute gate failed.')
    -- Record raw observations; stable metadata alone does not prove correct coverage.
    local previous,stable,after=nil,0,nil
    for _=1,40 do
        LrTasks.sleep(0.25);guard()
        after=photo:getDevelopSettings()
        local fingerprint=json(after.MaskGroupBasedCorrections or {})
        if fingerprint==previous then stable=stable+1 else previous=fingerprint;stable=0 end
        if stable>=8 and #(after.MaskGroupBasedCorrections or {})>0 then break end
    end
    local changes={}
    for k,v in pairs(before) do
        if k~='MaskGroupBasedCorrections' and json(after[k])~=json(v) then changes[#changes+1]=k end
    end
    writeJson(path('responses/' .. id .. '-people-settings.json'), after)
    return {photoId=expectedId, mode=mode, requestedSubtype=case[1], requestedReferencePoint=case[2],
        groups=after.MaskGroupBasedCorrections or {}, nonMaskChanges=changes, stableTicks=stable}
end

local function status(state, err)
    writeJson(path('status.json'), {
        state = state, readOnly = false,
        version = LrApplication.versionString(),
        pluginVersion = '0.8.0', communicationRoot = Config.root,
        updatedAt = os.time(), error = err,
        capabilities = { inspect = true, preview = true, setExposure = true, masks = true, addRadial = true,
            fullSettings = true, applyNumeric = true, applyStructured = true,
            preparePhoto = true, exportFullJpeg = true, jsonArrays = true, extendedDevelop = true, nativeMasks = true, peopleProbe = true, peopleProbeVersion = 3 },
    })
end
function M.stop() M.running = false end
function M.start()
    if M.running then return end
    M.running = true
    LrTasks.startAsyncTask(function()
        local ok, err = LrTasks.pcall(function()
            for _, name in ipairs({'requests','responses','previews','backups','exports'}) do
                LrFileUtils.createAllDirectories(path(name))
            end
            status('ready')
            local deadline, lastStatus = os.time() + Config.sessionSeconds, 0
            while M.running and os.time() < deadline do
                for requestPath in LrFileUtils.files(path('requests')) do
                    local id = LrPathUtils.leafName(requestPath):match('^([a-f0-9]+)%.request$')
                    if id and #id == 32 and not LrFileUtils.exists(path('responses/' .. id .. '.json')) then
                        local succeeded, result = LrTasks.pcall(function()
                            local body = LrFileUtils.readFile(requestPath)
                            assert(#body <= 16384, 'Request too large.')
                            local prepareId, prepareExpires, inputPath, inputCatalog = body:match('^prepare\n([0-9]*)\n([0-9]+)\n([a-f0-9]+)\n([a-f0-9%-]+)\n$')
                            if prepareId then
                                assert(os.time() <= tonumber(prepareExpires), 'Request expired.')
                                return preparePhoto(prepareId, inputPath, inputCatalog)
                            end
                            local exportId, exportExpires, exportToken = body:match('^export\n([0-9]+)\n([0-9]+)\n([a-f0-9]+)\n$')
                            if exportId then
                                assert(os.time() <= tonumber(exportExpires), 'Request expired.')
                                return batch.export(id, exportId, exportToken)
                            end
                            local batchPhoto, batchExpires, batchPayload = body:match('^apply\n([0-9]+)\n([0-9]+)\n(.*)$')
                            if batchPhoto then
                                assert(os.time() <= tonumber(batchExpires), 'Request expired.')
                                return batch.apply(id, batchPhoto, batchPayload, tonumber(batchExpires))
                            end
                            local action, photoId, expires = body:match('^([a-z]+)\n([0-9]*)\n([0-9]+)\n$')
                            local value, expectedBefore
                            if not action then
                                action, photoId, expires, value, expectedBefore = body:match('^([a-z]+)\n([0-9]+)\n([0-9]+)\n([%d%.%-]+)\n([%d%.%-]+)\n$')
                                assert(action == 'setexposure' or action == 'addradial' or action == 'tuneradial', 'Unsupported parameterized action.')
                            end
                            assert(action, 'Malformed request.')
                            assert(os.time() <= tonumber(expires), 'Request expired; submit a fresh one.')
                            if action == 'peopleskinall' or action == 'peopleskinleft' or action == 'peopleskinright' or action == 'peoplebuiltin' or action == 'peopledirectleft' or action == 'peopledirectright' or action == 'peoplegrouprefs' or action == 'peoplepointarray' or action == 'peoplepointmap' or action == 'peopletwogroups' or action == 'peoplecandidates' then return peopleProbe(id, photoId, action) end
                            if action == 'inspect' then return inspect(photoId) end
                            if action == 'settings' then return batch.settings(id, photoId) end
                            if action == 'masks' then return maskData(photoId) end
                            if action == 'maskprobe' then return maskProbe(photoId) end
                            if action == 'addradial' then return addRadial(id, photoId, value, expectedBefore) end
                            if action == 'tuneradial' then return tuneRadial(id, photoId, value, expectedBefore) end
                            if action == 'preview' then return preview(id, photoId) end
                            if action == 'setexposure' then return setExposure(id, photoId, value, expectedBefore) end
                            if action == 'stop' then M.stop(); return { stopped = true } end
                            error('Unsupported action: ' .. action)
                        end)
                        writeJson(path('responses/' .. id .. '.json'), {
                            requestId = id, ok = succeeded, completedAt = os.time(),
                            result = succeeded and result or nil,
                            error = not succeeded and tostring(result) or nil,
                        })
                        deadline = os.time() + Config.sessionSeconds -- idle timeout for long batches
                    end
                end
                if os.time() - lastStatus >= 5 then status('ready'); lastStatus = os.time() end
                LrTasks.sleep(0.5)
            end
        end)
        M.running = false
        status(ok and 'stopped' or 'error', not ok and tostring(err) or nil)
    end)
end
return M
