local UEHelpers = require("UEHelpers")

local GetWorld = UEHelpers.GetWorld
local GetKismetSystemLibrary = UEHelpers.GetKismetSystemLibrary

--- @param cmd string

local wprint = print
local function print(...)
    wprint(..., "\n")
end

function ExecCmd(param, value)
    local cmd = param.." "..value

    ExecuteInGameThread(function()
        local ksl = GetKismetSystemLibrary()
        local engine = FindFirstOf("Engine")
        if (ksl and ksl:IsValid()) and (engine and engine:IsValid()) then
            ksl:ExecuteConsoleCommand(engine, cmd, nil)
        end
    end)
end

local isFPSOn = false
function ToggleFPS()
    isFPSOn = not isFPSOn
    ExecCmd("stat", "fps")
end

local globalTweaks = {
    ["r.Lumen.Reflections.Allow"] = {1,0,0},
    ["r.Lumen.AsyncCompute"] = {1,0,0},
    ["r.RDG.AsyncCompute"] = {1,0,0},
    ["r.DepthOfFieldQuality"] = {2,0,0},
    ["r.Lumen.ScreenProbeGather.DownsampleFactor"] = {16,32,32},
    ["r.Tonemapper.Sharpen"] = {-1,2,2},
    ["r.Lumen.ScreenProbeGather.Temporal.MaxFramesAccumulated"] = {10,32,64},
    ["r.VolumetricCloud"] = {1,0,0},
    ["r.LightShaftQuality"] = {1,0,0},
    ["r.Shadow.Virtual.Enable"] = {1,0,0},
    ["r.Shadow.CSM.MaxCascades"] = {10,1,1},
    ["r.Shadow.Denoiser"] = {2,0,0},
    ["r.Shadow.RadiusThreshold"] = {0.01,0.03,0.03},
    ["r.Shadow.MaxCSMResolution"] = {2048,8192,8192},
    ["r.SSR.Quality"] = {3,2,1},
    ["r.PostProcessing.DisableMaterials"] = {0,0,1},
    ["r.Nanite.ShadeBinningMode"] = {0,1,1},
    ["r.Nanite.ViewMeshLODBias.Offset"] = {0,1,2},
    ["r.Nanite.Tessellation"] = {1,0,0},

    ["r.StaticMeshLODDistanceScale"] = {1,0.25,0.25},
    ["r.Nanite"] = {1,0,0},
    ["foliage.MaxEndCullDistance"] = {0,0,0},
    ["foliage.CullAll"] = {0,0,0},
    ["r.Shadow.DistanceScale"] = {1,0.32,0.32},
    ["r.Lumen.HardwareRayTracing"] = {1,1,0},
    ["foliage.LODDistanceScale"] = {1,1,1}
}

local perMapTweaks = {
    -- Reset any configs altered by perMapTweaks in Transition
    ["Level /Game/Map/TransitionMap/Transition.Transition:PersistentLevel"] = {
        ["r.StaticMeshLODDistanceScale"] = {1,0.25,0.25},
        ["r.Nanite"] = {1,0,0},
        ["foliage.MaxEndCullDistance"] = {0,0,0},
        ["foliage.CullAll"] = {0,0,0},
        ["r.Shadow.DistanceScale"] = {1,0.32,0.32},
        ["r.Lumen.HardwareRayTracing"] = {1,0,0},
        ["foliage.LODDistanceScale"] = {1,1,1},
    },
    ["Level /Game/Map/Tumblewood/Trenches.Trenches:PersistentLevel"] = {
        ["r.Nanite"] = {1,1,1},
        ["r.Lumen.HardwareRayTracing"] = {1,0,0}
    },
    ["Level /Game/Map/RussianBuilding/RussianBuilding.RussianBuilding:PersistentLevel"] = {
        ["foliage.MaxEndCullDistance"] = {0,1024,1024},
        ["r.Lumen.HardwareRayTracing"] = {1,1,1},
        ["foliage.CullAll"] = {0,0,1}
    },
    ["Level /Game/Map/WornHouse/WornHouse.WornHouse:PersistentLevel"] = {
        ["foliage.MaxEndCullDistance"] = {0,2048,2048},
        ["r.Shadow.DistanceScale"] = {1,1,1},
        ["r.Lumen.HardwareRayTracing"] = {1,0,0},
    },
    ["Level /Game/Map/AirSoft/AirSoft.AirSoft:PersistentLevel"] = {
        ["r.Lumen.HardwareRayTracing"] = {1,0,0},
    },
    ["Level /Game/Map/Hospital/Hospital.Hospital:PersistentLevel"] = {
        ["r.Lumen.HardwareRayTracing"] = {1,0,0},
        ["r.StaticMeshLODDistanceScale"] = {1,0.05,0.05},
    },
    ["Level /Game/Map/BombHouse/BombHouse.BombHouse:PersistentLevel"] = {
        ["r.Lumen.HardwareRayTracing"] = {1,0,0},
    },
    ["Level /Game/Map/PublicPool/PublicPool.PublicPool:PersistentLevel"] = {
        ["foliage.MaxEndCullDistance"] = {0,1024,1024},
        ["r.Lumen.HardwareRayTracing"] = {1,0,0},
    },
    ["Level /Game/Map/Rome/Rome.Rome:PersistentLevel"] = {
        ["r.Lumen.HardwareRayTracing"] = {1,0,0},
    },
    ["Level /Game/Map/CQB/CQB.CQB:PersistentLevel"] = {
        ["foliage.LODDistanceScale"] = {1,0.25,0.25},
        ["foliage.MaxEndCullDistance"] = {0,4096,2048},
        ["r.Lumen.HardwareRayTracing"] = {1,1,0},
    },
    ["Level /Game/Map/Backrooms/TheBackrooms.TheBackrooms:PersistentLevel"] = {
        ["r.Nanite"] = {1,1,1},
        ["r.Lumen.HardwareRayTracing"] = {1,0,0},
    },
}
perMapTweaks["Level /Game/Map/Lobby/Lobby.Lobby:PersistentLevel"] = perMapTweaks["Level /Game/Map/TransitionMap/Transition.Transition:PersistentLevel"]

local currentTweakLevel = 2

function RunBorderUpdate()
    ExecuteInGameThread(function()
        local ok, err = pcall(function()
            local fisheye_enabled = currentTweakLevel < 3
            local chars = FindAllOf("Bodycam_Player_C")
            if not chars then return end
            for _, char in ipairs(chars) do
                if char and char:IsValid() then
                    local comp = char.BodycamBorders
                    if comp and comp:IsValid() then
                        comp:SetVisibility(fisheye_enabled, true)
                    end
                end
            end
        end)
        if not ok then
            print("[CQBTweaks] RunBorderUpdate error: "..tostring(err))
        end
    end)
    return false
end

local blockedIndexes = {}
local loadedMapTweaks = {}
local loadedMapTweaksName = nil
function loadMapTweaks()
    print("[CQBTweaks] Loading map-specific tweaks at performance level "..currentTweakLevel)
    RunBorderUpdate()
    blockedIndexes = {}
    for i, v in pairs(loadedMapTweaks) do
        blockedIndexes[i] = true
        ExecCmd(i,v[currentTweakLevel])
    end
end
function SetTweakLevel(id)
    id=id+1
    if id ~= currentTweakLevel then
        print("[CQBTweaks] Set performance level to "..id)
    end
    currentTweakLevel = id

    loadMapTweaks()

    for i,v in pairs(globalTweaks) do
        if not blockedIndexes[i] then
            ExecCmd(i,v[id])
        end
    end
end

local taskQueue = {}
function TaskQueueSubmit(func)
    local world = GetWorld()
    if world and world:IsValid() and world.PersistentLevel then
        return func()
    end
    table.insert(taskQueue, func)
end
function FlushTaskQueue()
    local world = GetWorld()
    if not (world and world:IsValid() and world.PersistentLevel) then
        return
    end
    for _,task in pairs(taskQueue) do
        task()
    end
    taskQueue = {}
end

local lastLevelLoad = 0
local needsLoad = false
function LoadMapTweaksLate()
    if needsLoad and os.clock() - lastLevelLoad > 2 then
        needsLoad = false
        ExecuteWithDelay(1000, LoadMapTweaksLate)
        ExecuteWithDelay(2000, LoadMapTweaksLate)
        ExecuteWithDelay(3000, LoadMapTweaksLate)
        TaskQueueSubmit(function() loadMapTweaks() end)
    end
    FlushTaskQueue()
end

local hasDoneInit = false
NotifyOnNewObject("/Script/Engine.Level", function(ConstructedObject)
    lastLevelLoad = os.clock()
    local recognizedLevel = ConstructedObject:GetFullName()
    print("[CQBTweaks] Level loaded: "..recognizedLevel)
    local hasTweaks = perMapTweaks[recognizedLevel]
    if hasTweaks then
        print("[CQBTweaks] Swapped map-specific tweaks.")
        -- Load early to avoid lobby bug
        loadedMapTweaks = hasTweaks
        loadedMapTweaksName = recognizedLevel
        needsLoad = true
        TaskQueueSubmit(function() loadMapTweaks() end)
    end
    if not hasDoneInit and recognizedLevel == "Level /Game/Map/Lobby/Lobby.Lobby:PersistentLevel" then
        hasDoneInit = true
        LoadMapTweaksLate()
        SetTweakLevel(1)
        if not isFPSOn then
            ToggleFPS()
        end
    end
end)

RegisterConsoleCommandHandler("fpsb", function(OutputDevice)
    SetTweakLevel(1)
    return true
end)

RegisterKeyBind(Key.F5, ToggleFPS)
RegisterKeyBind(Key.F1, function() SetTweakLevel(0) end)
RegisterKeyBind(Key.F2, function() SetTweakLevel(1) end)
RegisterKeyBind(Key.F3, function() SetTweakLevel(2) end)

LoopAsync(1000, RunBorderUpdate);

SetTweakLevel(1)
loadMapTweaks()
LoadMapTweaksLate()
ToggleFPS()