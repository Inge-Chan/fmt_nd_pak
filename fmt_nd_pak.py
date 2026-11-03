#fmt_nd_pak.py - Naughty Dog ".pak" plugin for Rich Whitehouse's Noesis
#Author: alphaZomega 
#Special Thanks: icemesh 
Version = 'v1.53 + Inge 2026-11-03'


#Options: These are global options that change or enable/disable certain features
#Option															Effect
GlobalScale = 100												# Set the scale of the imported model
NoDialog = False												# Disable the UI dialog window on import
LoadBaseSkeleton = True											# Attempt to load a base skeleton for every rigged model missing bones
LoadAllLODs	= False												# Load lower detail LODs onto the model. Lower detail LODs will be disabled on exported paks with this option enabled
LoadTextures = False											# Load textures onto the model
LoadAnimations = True											# Scan the pak for animations and report what can be decoded
ConvertTextures = True											# Convert normal maps to put normal X in the red channel and normal Y in the green channel (standard format)
FlipUVs = False													# Flip UVs and flip texture images rightside-up (NOT IMPLEMENTED)
LoadAllTextures = False											# Load all textures onto a model, rather than only color and normal maps
ReadColors = False												# Read vertex colors
PrintMaterialParams = False										# Print out all material parameters in the debug log when importing
texoutExt = ".tga"												# Extension of texture files (change to load textures of a specific type in Blender)
gameName = "TLOUP1"												# Default game name
ReparentHelpers = 2												# Parents helper bones based on their names, mostly for TLOU models. Set to 2 for Auto


from inc_noesis import *
from collections import namedtuple
import noewin
import json
import os
import re
import struct
import time
import inc_nd_config
import inc_nd_modelparts
import inc_nd_skeleton

configuration = inc_nd_config.load_config(os.path.join(noesis.getPluginsPath(), "python"))
BaseDirectories = configuration["baseDirectories"]
gameName = configuration["defaultGame"]

try:
	import inc_nd_anim
	HaveAnimSupport = True
except Exception as _animImportError:
	print("[NDAnim] animation module unavailable:", _animImportError)
	HaveAnimSupport = False

class DialogOptions:
	def __init__(self):
		self.doLoadTex = LoadTextures
		self.doLoadBase = LoadBaseSkeleton
		self.doConvertTex = ConvertTextures
		self.doFlipUVs = FlipUVs
		self.doLODs = LoadAllLODs
		self.loadAllTextures = LoadAllTextures
		self.printMaterialParams = PrintMaterialParams
		self.reparentHelpers = ReparentHelpers
		self.readColors = ReadColors
		self.baseSkeleton = None
		self.width = 600
		self.height = 850
		self.texDicts = None
		self.textureIndexAttempts = set()
		self.gameName = gameName
		self.currentDir = ""
		self.isTLOU2 = False
		self.isTLOUP1 = False
		self.dialog = None

dialogOptions = DialogOptions()
ResItemPaddingSz = 32
SkinSamples = None # bind-space (position, [(bone, weight)]) of loaded meshes, for strap anchoring
SkinSampleExcludedMeshes = r'hair|head|eye|lash|brow|teeth|tongue|caruncle|tear|bkpk|backpack|strap'

def geometrySubmeshLayout(version, modern):
	return (192, 8) if modern and version == 57 else (176, 0)


def registerNoesisTypes():
	handle = noesis.register("Naughty Dog PAK", ".pak")
	noesis.setTypeExportOptions(handle, "-noanims -notex")
	noesis.addOption(handle, "-nodialog", "Do not display dialog window", 0)
	noesis.addOption(handle, "-ndgame", "Override the game ID: U4, TLL, TLOUP1, TLOU2", noesis.OPTFLAG_WANTARG)
	noesis.addOption(handle, "-ndrebuildtextures", "Rebuild the local texture dictionary index for the selected game", 0)
	noesis.addOption(handle, "-ndtextures", "Load textures, building a missing local dictionary index automatically", 0)
	noesis.addOption(handle, "-t", "Textures only; do not inject geometry data", 0)
	noesis.addOption(handle, "-bones", "Write bone positions", 0)
	noesis.addOption(handle, "-lods", "Import/Export with all LODs", 0)
	noesis.addOption(handle, "-meshfile", "Export using a given source mesh filepath", noesis.OPTFLAG_WANTARG)
	noesis.addOption(handle, "-texfolder", "Export using a given textures folder for embedding", noesis.OPTFLAG_WANTARG)
	noesis.addOption(handle, "-anims", "Load animations from the pak", 0)
	noesis.addOption(handle, "-animfile", "Load animations from another pak", noesis.OPTFLAG_WANTARG)
	noesis.addOption(handle, "-modelparts", "Additional model paks separated by semicolons, or @JSON manifest", noesis.OPTFLAG_WANTARG)
	noesis.addOption(handle, "-animclip", "Load only this exact animation name", noesis.OPTFLAG_WANTARG)
	noesis.addOption(handle, "-animadditive", "Preview additive clips on their bind pose (experimental)", 0)
	noesis.addOption(handle, '-ndlegacyhelpers', 'Use legacy guessed helper bones for comparison', 0)
	noesis.setHandlerTypeCheck(handle, pakCheckType)
	noesis.setHandlerLoadModel(handle, pakLoadModel)
	noesis.setHandlerWriteModel(handle, pakWriteModel)
	noesis.setTypeSharedModelFlags(handle, (noesis.NMSHAREDFL_WANTGLOBALARRAY))
	return 1
	
def pakCheckType(data):
	bs = NoeBitStream(data)
	magic = bs.readUInt()
	if (magic == 2681 or magic == 2685) and magic != 68217 and magic != 2147486329:
		return 1
	else: 
		print("Fatal Error: Unknown file magic: " + str(hex(magic)))
		return 0
		
def getGameName():
	if noesis.optWasInvoked("-ndgame"):
		selected = noesis.optGetArg("-ndgame").upper()
		if selected not in inc_nd_config.GAME_IDS:
			raise ValueError("unknown game ID: " + selected)
		return selected
	return inc_nd_config.detect_game(rapi.getInputName(), BaseDirectories, gameName)

def findNextOf(bs, integer, is64=False):
	start = bs.tell()
	while not bs.checkEOF() and ((is64 and bs.readInt64()) or bs.readInt()) != integer:
		pass
	output = bs.tell()
	bs.seek(start)
	return output

def readStringAt(bs, offset):
	start = bs.tell()
	bs.seek(offset)
	output = bs.readString()
	bs.seek(start)
	return output
	
def readUIntAt(bs, offset):
	start = bs.tell()
	bs.seek(offset)
	output = bs.readUInt()
	bs.seek(start)
	return output 
	
def writeUIntAt(bs, offset, value):
	start = bs.tell()
	bs.seek(offset)
	bs.writeUInt(value)
	bs.seek(start)
	
def findRootDir(path):
	return inc_nd_config.find_root_dir(path)

def readFileBytes(filepath, address, size):
	with open(filepath, 'rb') as f:
		f.seek(address)
		return f.read(size)

def readTextureDictionaryMetadata(path):
	with open(path, "rb") as source:
		source.seek(0, os.SEEK_END)
		fileSize = source.tell()
		source.seek(0)
		header = source.read(24)
		if len(header) != 24:
			raise ValueError("truncated texture dictionary header: " + path)
		magic = struct.unpack_from("<I", header, 0)[0]
		if magic not in (2681, 68217, 2147486329, 2685, 68221):
			raise ValueError("invalid texture dictionary magic: " + path)
		pageCount, pageTable = struct.unpack_from("<II", header, 16)
		tableEnd = pageTable + pageCount * 12
		if pageCount == 0 or pageTable < 24 or tableEnd > fileSize:
			raise ValueError("invalid texture dictionary page table: " + path)
		source.seek(pageTable + (pageCount - 1) * 12)
		pageOffset, pageSize = struct.unpack("<II", source.read(8))
		metadataSize = pageOffset + pageSize
		if metadataSize < tableEnd or metadataSize > fileSize:
			raise ValueError("invalid texture dictionary metadata range: " + path)
		source.seek(0)
		metadata = source.read(metadataSize)
		if len(metadata) != metadataSize:
			raise ValueError("truncated texture dictionary metadata: " + path)
		return metadata

def loadTextureHashCache():
	path = os.path.join(noesis.getPluginsPath(), "python", "NDTextureHashes.json")
	try:
		with open(path, "r", encoding="utf-8-sig") as source:
			cache = json.load(source)
	except IOError:
		return {}
	except ValueError as error:
		print("[NDTextures] invalid texture index; rebuild needed:", error)
		return {}
	if not isinstance(cache, dict):
		print("[NDTextures] invalid texture index; expected a JSON object")
		return {}
	return cache

def generateDummyTexture4px(rgbaColor, name="Dummy"):
	imageByteList = []
	for i in range(16):
		imageByteList.extend(rgbaColor)
	imageData = struct.pack("<" + 'B'*len(imageByteList), *imageByteList)
	imageData = rapi.imageDecodeRaw(imageData, 4, 4, "r8g8b8a8")
	return NoeTexture(name, 4, 4, imageData, noesis.NOESISTEX_RGBA32)	
	
def moveChannelsRGBA(sourceBytes, sourceChannel, sourceWidth, sourceHeight, targetBytes, targetChannel, targetWidth, targetHeight):
	resizedSourceBytes = rapi.imageResample(sourceBytes, sourceWidth, sourceHeight, targetWidth, targetHeight)
	outputTargetBytes = copy.copy(targetBytes)
	for i in range(int(len(resizedSourceBytes)/16)):
		for b in range(4):
			outputTargetBytes[i*16 + b*4 + targetChannel] = resizedSourceBytes[i*16 + b*4 + sourceChannel]
	return outputTargetBytes

def encodeImageData(data, width, height, fmtName):
	outputData = NoeBitStream()
	mipWidth = width
	mipHeight = height
	mipCount = 0
	decodeFmt, encodeFmt, bpp = getDXTFormat(fmtName)
	
	if encodeFmt != None:
		while mipWidth > 2 or mipHeight > 2:
			mipData = rapi.imageResample(data, width, height, mipWidth, mipHeight)
			try:
				dxtData = rapi.imageEncodeDXT(mipData, bpp, mipWidth, mipHeight, encodeFmt)
			except:
				dxtData = rapi.imageEncodeRaw(mipData, mipWidth, mipHeight, encodeFmt)
			outputData.writeBytes(dxtData)
			if mipWidth > 2: 
				mipWidth = int(mipWidth / 2)
			if mipHeight > 2: 
				mipHeight = int(mipHeight / 2)
			mipCount += 1
		
	return outputData.getBuffer(), mipCount
	
def getDXTFormat(fmtName):
	bpp = 8
	decFmt = encFmt = None
	if fmtName.count("Bc1"):
		encFmt = noesis.NOE_ENCODEDXT_BC1
		decFmt = noesis.FOURCC_DXT1
		bpp = 4
	elif fmtName.count("Bc3"):
		encFmt = noesis.NOE_ENCODEDXT_BC3
		decFmt = noesis.FOURCC_BC3
	elif fmtName.count("Bc4"):
		encFmt = noesis.NOE_ENCODEDXT_BC4
		decFmt = noesis.FOURCC_BC4
		bpp = 4
	elif fmtName.count("Bc5"):
		encFmt = noesis.NOE_ENCODEDXT_BC5
		decFmt = noesis.FOURCC_BC5
	elif fmtName.count("Bc6"): 
		encFmt = noesis.NOE_ENCODEDXT_BC6S
		decFmt = noesis.FOURCC_BC6S
	elif fmtName.count("Bc7"): 
		encFmt = noesis.NOE_ENCODEDXT_BC7
		decFmt = noesis.FOURCC_BC7
	elif re.search(r"[RGBA]\d\d?", fmtName):
		fmtName = fmtName.split("_")[0].lower()
		encFmt = decFmt = fmtName
	return decFmt, encFmt, bpp
	
	
def recombineNoesisMeshes(mdl):
	
	meshesBySourceName = {}
	for mesh in mdl.meshes:
		meshesBySourceName[mesh.sourceName] = meshesBySourceName.get(mesh.sourceName) or []
		meshesBySourceName[mesh.sourceName].append(mesh)
		
	combinedMeshes = []
	for sourceName, meshList in meshesBySourceName.items():
		newPositions = []
		newUV1 = []
		newUV2 = []
		newUV3 = []
		newTangents = []
		newWeights = []
		newIndices = []
		newColors = []
		for mesh in meshList:
			tempIndices = []
			for index in mesh.indices:
				tempIndices.append(index + len(newPositions))
			newPositions.extend(mesh.positions)
			newUV1.extend(mesh.uvs)
			newUV2.extend(mesh.lmUVs)
			newUV3.extend(mesh.uvxList[0] if len(mesh.uvxList) > 0 else [])
			newColors.extend(mesh.colors)
			newTangents.extend(mesh.tangents)
			newWeights.extend(mesh.weights)
			newIndices.extend(tempIndices)
			
		combinedMesh = NoeMesh(newIndices, newPositions, meshList[0].sourceName, meshList[0].sourceName, mdl.globalVtx, mdl.globalIdx)
		combinedMesh.setTangents(newTangents)
		combinedMesh.setWeights(newWeights)
		combinedMesh.setUVs(newUV1)
		combinedMesh.setUVs(newUV2, 1)
		combinedMesh.setUVs(newUV3, 2)
		combinedMesh.setColors(newColors)
		if len(combinedMesh.positions) > 65535:
			print("Warning: Mesh exceeds the maximum of 65535 vertices (has", str(len(combinedMesh.positions)) + "):\n	", combinedMesh.name)
		else:
			combinedMeshes.append(combinedMesh)
		
	return combinedMeshes

fullGameNames = ["Uncharted 4", "The Lost Legacy", "The Last of Us P1", "The Last of Us P2"]
gamesList = [ "U4", "TLL",  "TLOUP1", "TLOU2"]

# ---- Dynamic skeleton file scanner ----
def _scanSkelFiles(baseDir):
	"""Recursively scan baseDir for *skel*.pak files, return list of relative paths."""
	found = []
	if not os.path.isdir(baseDir):
		return found
	for root, dirs, files in os.walk(baseDir):
		for f in files:
			if "skel" in f.lower() and f.lower().endswith(".pak"):
				relPath = os.path.relpath(os.path.join(root, f), baseDir)
				found.append(relPath)
	found.sort()
	return found

_skelPakIdCache = {}

def _skelPakId(path):
	"""SkelID of a skeleton pak: the u32 sitting 20 bytes after the
	JOINT_HIERARCHY type hash in the item header. The same id is stored at
	+0x20 of every ANIM payload targeting that skeleton, which makes an
	exact match possible instead of filename guessing (guessing famously
	pairs ellie anims with t1x-ellie-skel, whose joints are shifted)."""
	if path in _skelPakIdCache:
		return _skelPakIdCache[path]
	sid = None
	try:
		with open(path, "rb") as f:
			data = f.read(0x20000)
		sig = struct.pack("<Q", 0x50CAF5257D6A140B)
		i = data.find(sig)
		if i >= 0 and i + 24 <= len(data):
			sid = struct.unpack_from("<I", data, i + 20)[0]
	except Exception:
		sid = None
	_skelPakIdCache[path] = sid
	return sid

def _findSkelPakForAnims(animClips, gameName):
	"""Path of the skeleton pak whose SkelID matches the clips, or None."""
	counts = {}
	for clip in animClips:
		sid = getattr(clip, "skelId", None)
		if sid:
			counts[sid] = counts.get(sid, 0) + 1
	if not counts:
		return None
	target = sorted(counts.items(), key=lambda kv: -kv[1])[0][0]
	baseDir = BaseDirectories.get(gameName, "")
	for rel in skelFiles.get(gameName, []):
		full = os.path.join(baseDir, rel)
		if _skelPakId(full) == target:
			return full
	return None

def _readPakAnimations(pak):
	if not HaveAnimSupport:
		return []
	if pak.animClips is None:
		if not pak.pakPageEntries:
			pak.readPakHeader()
		pak.animReader = inc_nd_anim.NDAnimReader(pak)
		pak.animClips = pak.animReader.read()
	return pak.animClips

def _pakSkeletonId(pak):
	for source in (pak, getattr(pak, 'basePak', None)):
		if source is not None and source.jointOffset:
			offset, start = source.jointOffset
			return readUIntAt(source.bs, start + offset + 0x34)
	return None

def _buildBaseSkeletons(skelFileList):
	"""Build hint->filename mapping from a list of skeleton file paths.
	Generates multiple hint variants per file for robust matching.
	When multiple files share the same hint, the LAST one wins (alphabetically later).
	Matching uses longest-hint-wins, so more specific hints take priority."""
	mapping = {}
	for relPath in skelFileList:
		fname = os.path.basename(relPath).lower()
		# Strip common skel suffixes
		for suffix in ["-skel.pak", "_skel.pak", "-base.pak", "_base.pak", "-realskel.pak", ".pak"]:
			if fname.endswith(suffix):
				core = fname[:-len(suffix)]
				break
		else:
			core = fname.replace(".pak", "")

		hints = set()
		hints.add(core)  # e.g. "t1x-joel"

		# Strip game prefixes for cleaner matching
		for prefix in ["t1x-", "t1-", "t2-"]:
			if core.startswith(prefix):
				stripped = core[len(prefix):]
				if stripped:
					hints.add(stripped)  # e.g. "joel"

		# Add progressive prefix segments for partial matching
		# e.g. "npc-normal" -> also add "npc" so "anim-npc-alert" can match
		# e.g. "base-male-crowd" -> also add "base-male", "base"
		for h in list(hints):
			segments = h.split("-")
			for i in range(1, len(segments)):
				prefix_hint = "-".join(segments[:i])
				if len(prefix_hint) >= 3:  # avoid overly short hints
					hints.add(prefix_hint)

		# Also add the parent directory as a compound hint for disambiguation
		parts = relPath.replace("/", "\\").split("\\")
		if len(parts) >= 2:
			dirName = parts[-2].lower()  # e.g. "actor97"
			for h in list(hints):
				hints.add(dirName + "\\" + h)

		# For each hint, store the relPath
		for h in hints:
			if h:
				mapping[h] = relPath

	return mapping

# Dynamically populate skelFiles and baseSkeletons for games with valid base directories
skelFiles = {}
baseSkeletons = {}
for _gn in gamesList:
	_bd = BaseDirectories.get(_gn, "")
	if os.path.isdir(_bd):
		_scanned = _scanSkelFiles(_bd)
		if _scanned:
			skelFiles[_gn] = _scanned
			baseSkeletons[_gn] = _buildBaseSkeletons(_scanned)
			print("[SkelScan] %s: found %d skeleton files, %d hints" % (_gn, len(_scanned), len(baseSkeletons[_gn])))
	if _gn not in skelFiles:
		skelFiles[_gn] = []
	if _gn not in baseSkeletons:
		baseSkeletons[_gn] = {}

dxFormat = {
	0: "Invalid",
	2: "R32G32B32A32_Float",
	3: "R32G32B32A32_Uint",
	4: "R32G32B32A32_Sint",
	6: "R32G32B32_Float",
	7: "R32G32B32_Uint",
	8: "R32G32B32_Sint",
	0xA: "R16G16B16A16_Float",
	0xB: "R16G16B16A16_Unorm",
	0xC: "R16G16B16A16_Uint",
	0xD: "R16G16B16A16_Snorm",
	0xE: "R16G16B16A16_Sint",
	0x10: "R32G32_Float",
	0x11: "R32G32_Uint",
	0x12: "R32G32_Sint",
	0x13: "R32G8X24",
	0x14: "D32S8X24",
	0x15: "R32X8X24",
	0x16: "X32G8X24",
	0x18: "R10G10B10A2_Unorm",
	0x19: "R10G10B10A2_Uint",
	0x1A: "R11G11B10_Float",
	0x1C: "R8G8B8A8_Unorm",
	0x1D: "R8G8B8A8_UnormSrgb",
	0x1E: "R8G8B8A8_Uint",
	0x1F: "R8G8B8A8_Snorm",
	0x20: "R8G8B8A8_Sint",
	0x22: "R16G16_Float",
	0x23: "R16G16_Unorm",
	0x24: "R16G16_Uint",
	0x25: "R16G16_Snorm",
	0x26: "R16G16_Sint",
	0x27: "R32Typeless",
	0x28: "D32_Float",
	0x29: "R32_Float",
	0x2A: "R32_Uint",
	0x2B: "R32_Sint",
	0x2C: "R24G8_Typeless",
	0x2D: "D24S8",
	0x2E: "R24X8_Unorm",
	0x2F: "X24G8",
	0x31: "R8G8_Unorm",
	0x32: "R8G8_Uint",
	0x33: "R8G8_Snorm",
	0x34: "R8G8_Sint",
	0x35: "R16_Typeless",
	0x36: "R16_Float",
	0x37: "D16_Unorm",
	0x38: "R16_Unorm",
	0x39: "R16_Uint",
	0x3A: "R16_Snorm",
	0x3B: "R16_Sint",
	0x3D: "R8_Unorm",
	0x3E: "R8_Uint",
	0x3F: "R8_Snorm",
	0x40: "R8_Sint",
	0x41: "A8_Unorm",
	0x42: "R1_Unorm",
	0x46: "Bc1_Typeless",
	0x47: "Bc1_Unorm",
	0x48: "Bc1_UnormSrgb",
	0x49: "Bc2_Typeless",
	0x4A: "Bc2_Unorm",
	0x4B: "Bc2_UnormSrgb",
	0x4C: "Bc3_Typeless",
	0x4D: "Bc3_Unorm",
	0x4E: "Bc3_UnormSrgb",
	0x4F: "Bc4_Typeless",
	0x50: "Bc4_Unorm",
	0x51: "Bc4_Snorm",
	0x52: "Bc5_Typeless",
	0x53: "Bc5_Unorm",
	0x54: "Bc5_Snorm",
	0x55: "B5G6R5_Unorm",
	0x56: "B5G5R5A1_Unorm",
	0x57: "B8G8R8A8_Unorm",
	0x58: "B8G8R8X8_Unorm",
	0x59: "R10G10B10A2_Unorm_2",
	0x5A: "B8G8R8A8_Unorm_2",
	0x5B: "B8G8R8A8_UnormSrgb",
	0x5C: "B8G8R8X8_Typeless",
	0x5D: "B8G8R8X8_UnormSrgb",
	0x5E: "Bc6Typeless",
	0x5F: "Bc6_Uf16",
	0x60: "Bc6_Sf16",
	0x61: "Bc7_Typeless",
	0x62: "Bc7_Unorm",
	0x63: "Bc7_UnormSrgb",
	0x64: "B16G16R16A16_Float" 
}

def _loadRawDataStarts():
	_path = noesis.getPluginsPath() + "python\\NDRawDataStarts.json"
	try:
		with open(_path, "r") as _f:
			return json.load(_f)
	except:
		return {}

def _saveRawDataStarts(data):
	_path = noesis.getPluginsPath() + "python\\NDRawDataStarts.json"
	with open(_path, "w") as _f:
		json.dump(data, _f, indent="\t")

gdRawDataStarts = _loadRawDataStarts()

# ensure all known game keys exist so code can do gdRawDataStarts[gameName] without KeyError
for _g in ["U4", "TLL", "TLOUP1", "TLOU2"]:
	gdRawDataStarts[_g] = gdRawDataStarts.get(_g) or {}

# ---- REMOVED hardcoded table; values are now persisted in NDRawDataStarts.json ----
# The first time a dict pak is opened its rawDataStart is computed dynamically from the
# pak header (last page offset + size) and written to NDRawDataStarts.json automatically.	
DoubleClickTimer = namedtuple("DoubleClickTimer", "name idx timer")

class openOptionsDialogWindow:
	
	def __init__(self, width=dialogOptions.width, height=dialogOptions.height, args=None):
		global dialogOptions
		args = args or {}
		
		self.width = width
		self.height = height
		self.pak = args.get("pak") or None
		self.path = self.pak.path or rapi.getInputName()
		self.name = rapi.getLocalFileName(self.path)
		self.loadItems = [self.name]
		self.fullLoadItems = [self.path]
		self.localDir = rapi.getDirForFilePath(self.path)
		self.localRoot = findRootDir(self.path)
		self.baseDir = BaseDirectories[gameName] 
		self.allFiles = []
		self.pakFiles = []
		self.subDirs = []
		self.pakIdx = 0
		self.baseIdx = -1
		self.loadIdx = 0
		self.dirIdx = 0
		self.gameIdx = 0
		self.localIdx = 0
		self.isOpen = True
		self.isCancelled = False
		dialogOptions.currentDir = self.localDir
		self.currentDirTxt = dialogOptions.currentDir
		self.clicker = DoubleClickTimer(name="", idx=0, timer=0)
		dialogOptions.dialog = self
		
	def setWidthAndHeight(self, width=dialogOptions.width, height=dialogOptions.width):
		self.width = width or self.width
		self.height = height or self.height
		
	def openOptionsButtonLoadEntry(self, noeWnd, controlId, wParam, lParam):
		self.isOpen = False
		self.noeWnd.closeWindow()
			
	def openOptionsButtonCancel(self, noeWnd, controlId, wParam, lParam):
		self.isCancelled = True
		self.isOpen = False
		self.noeWnd.closeWindow()
		
	def openOptionsButtonParentDir(self, noeWnd, controlId, wParam, lParam):
		if self.localIdx == 0: 
			self.localRoot = os.path.dirname(self.localRoot)
		else:
			self.baseDir = os.path.dirname(self.baseDir)
		self.setDirList()
		self.setPakList()
		if self.subDirs:
			self.dirList.selectString(self.subDirs[0])
	
	def selectBaseListItem(self, noeWnd, controlId, wParam, lParam):
		self.baseIdx = self.baseList.getSelectionIndex()
		dialogOptions.baseSkeleton = self.baseList.getStringForIndex(self.baseIdx)
		
	def selectPakListItem(self, noeWnd, controlId, wParam, lParam):
		self.pakIdx = self.pakList.getSelectionIndex()
		if self.clicker.name == "pakList" and self.pakIdx == self.clicker.idx and time.time() - self.clicker.timer < 0.25:
			if self.pakIdx == 0: #parent directory
				if dialogOptions.currentDir[-1:] == "\\":
					dialogOptions.currentDir = os.path.dirname(dialogOptions.currentDir)
				dialogOptions.currentDir = os.path.dirname(dialogOptions.currentDir)
				self.setPakList()
			elif self.pakIdx <= len(self.subDirs):
				dialogOptions.currentDir += "\\" + self.pakList.getStringForIndex(self.pakIdx)
				self.setPakList()
			elif self.pakList.getStringForIndex(self.pakIdx) not in self.loadItems:
				self.loadItems.append(self.pakList.getStringForIndex(self.pakIdx))
				self.fullLoadItems.append(dialogOptions.currentDir + self.pakList.getStringForIndex(self.pakIdx))
				self.loadList.addString(self.pakList.getStringForIndex(self.pakIdx))
				self.fullLoadItems = [x for _, x in sorted(zip(self.loadItems, self.fullLoadItems))]
				self.loadItems = sorted(self.loadItems)
		self.clicker = DoubleClickTimer(name="pakList", idx=self.pakIdx, timer=time.time())
	
	def selectLoadListItem(self, noeWnd, controlId, wParam, lParam):
		self.loadIdx = self.loadList.getSelectionIndex()
		if self.clicker.name == "loadList" and self.loadIdx == self.clicker.idx and time.time() - self.clicker.timer < 0.25 and self.loadItems[self.loadIdx] != self.name:
			self.loadList.removeString(self.loadItems[self.loadIdx])
			del self.loadItems[self.loadIdx]
			del self.fullLoadItems[self.loadIdx]
			self.loadIdx = self.loadIdx if self.loadIdx < len(self.loadItems) else self.loadIdx - 1
			self.loadList.selectString(self.loadItems[self.loadIdx])
			self.fullLoadItems = [x for _, x in sorted(zip(self.loadItems, self.fullLoadItems))]
			self.loadItems = sorted(self.loadItems)
		self.clicker = DoubleClickTimer(name="loadList", idx=self.loadIdx, timer=time.time())
	
	def selectGameBoxItem(self, noeWnd, controlId, wParam, lParam):
		global gameName
		if self.gameIdx != self.gameBox.getSelectionIndex():
			self.gameIdx = self.gameBox.getSelectionIndex()
			gameName = gamesList[self.gameIdx]
			restOfPath = dialogOptions.currentDir.replace(self.baseDir, "")
			self.baseDir = BaseDirectories[gameName]
			if self.localBox.getStringForIndex(self.localIdx) == "Base Directory":
				dialogOptions.currentDir = self.baseDir
				if restOfPath and os.path.isdir(self.baseDir + restOfPath):
					dialogOptions.currentDir = self.baseDir + restOfPath
				self.setPakList()
				
	def selectLocalBoxItem(self, noeWnd, controlId, wParam, lParam):
		if self.localIdx != self.localBox.getSelectionIndex():
			self.localIdx = self.localBox.getSelectionIndex()
			restOfPath = dialogOptions.currentDir.replace(self.localRoot, "").replace(self.baseDir, "")
			if self.localBox.getStringForIndex(self.localIdx) == "Base Directory":
				dialogOptions.currentDir = self.baseDir
				if restOfPath and os.path.isdir(self.baseDir + restOfPath):
					dialogOptions.currentDir = self.baseDir + restOfPath
			else:
				dialogOptions.currentDir = os.path.dirname(self.path)
				if restOfPath and os.path.isdir(self.localRoot + restOfPath):
					dialogOptions.currentDir = self.localRoot + restOfPath
			self.setPakList()
			
	def setBaseList(self, list_object=None, current_item=None):
		#current_item = current_item or baseSkeletons[gameName]["chloe" if gameName == "TLL" else "ellie" if gameName=="TLOU2" else "hero"]
		for path in skelFiles[gameName]:
			self.baseList.addString(path)
		if self.pak.geoOffset is None and self.pak.jointOffset is None and HaveAnimSupport:
			try:
				matched = _findSkelPakForAnims(_readPakAnimations(self.pak), gameName)
				if matched:
					relative = os.path.relpath(matched, BaseDirectories[gameName])
					self.baseList.selectString(relative)
					self.baseIdx = self.baseList.getSelectionIndex()
					dialogOptions.baseSkeleton = relative
					return
			except Exception as error:
				print('[NDAnim] skeleton selection failed:', error)
		lastFoundHint = ""
		for hint, fileName in baseSkeletons[gameName].items():
			if self.name.find(hint) != -1 and len(hint) > len(lastFoundHint):
				lastFoundHint = hint
				#print(fileName)
				#print(baseSkeletons[gameName])
				self.baseList.selectString(fileName)
				self.baseIdx = self.baseList.getSelectionIndex()
				dialogOptions.baseSkeleton = fileName
		
	def setGameBox(self, list_object=None, current_item=None):
		for i, name in enumerate(fullGameNames):
			self.gameBox.addString(name)
		self.gameBox.selectString(fullGameNames[gamesList.index(gameName)])
		self.gameIdx = self.gameBox.getSelectionIndex()
		
	def setLocalBox(self, list_object=None, current_item=None):
		for name in ["Local Folder", "Base Directory"]:
			self.localBox.addString(name)
		self.localBox.selectString("Local Folder")
		self.localIdx = self.localBox.getSelectionIndex()
	
	def checkLoadTexCheckbox(self, noeWnd, controlId, wParam, lParam):
		dialogOptions.doLoadTex = not dialogOptions.doLoadTex
		self.loadTexCheckbox.setChecked(dialogOptions.doLoadTex)
		
	def checkBaseCheckbox(self, noeWnd, controlId, wParam, lParam):
		dialogOptions.doLoadBase = not dialogOptions.doLoadBase
		self.loadBaseCheckbox.setChecked(dialogOptions.doLoadBase)
		
	def checkLODsCheckbox(self, noeWnd, controlId, wParam, lParam):
		dialogOptions.doLODs = not dialogOptions.doLODs
		self.LODsCheckbox.setChecked(dialogOptions.doLODs)
		
	def checkConvTexCheckbox(self, noeWnd, controlId, wParam, lParam):
		dialogOptions.doConvertTex = not dialogOptions.doConvertTex
		self.convTexCheckbox.setChecked(dialogOptions.doConvertTex)
		
	def checkFlipUVsCheckbox(self, noeWnd, controlId, wParam, lParam):
		dialogOptions.doFlipUVs = not dialogOptions.doFlipUVs
		self.flipUVsCheckbox.setChecked(dialogOptions.doFlipUVs)
		
	def checkLoadAllTexCheckbox(self, noeWnd, controlId, wParam, lParam):
		dialogOptions.loadAllTextures = not dialogOptions.loadAllTextures
		self.loadAllTexCheckbox.setChecked(dialogOptions.loadAllTextures)
		
	def checkReparentCheckbox(self, noeWnd, controlId, wParam, lParam):
		dialogOptions.reparentHelpers = not dialogOptions.reparentHelpers
		self.reparentCheckbox.setChecked(dialogOptions.reparentHelpers)
		
	def setLoadList(self):
		for item in self.loadItems:
			self.loadList.removeString(item)
		self.loadItems = [self.name]
		self.loadList.addString(self.loadItems[0])
		self.loadList.selectString(self.pak.path or rapi.getInputName())
		
	def setPakList(self):
		for name in self.allFiles:
			self.pakList.removeString(name)
		self.allFiles = [".."]
		self.pakFiles = []
		self.subDirs = []
		currentDirectory = dialogOptions.currentDir
		if not currentDirectory or not os.path.isdir(currentDirectory):
			print("[NDConfig] selected base directory is not configured or does not exist")
			currentDirectory = os.path.dirname(self.path)
			dialogOptions.currentDir = currentDirectory
		for item in os.listdir(currentDirectory):
			if os.path.isdir(os.path.join(dialogOptions.currentDir, item)):
				self.subDirs.append(item)
			if os.path.isfile(os.path.join(dialogOptions.currentDir, item)) and item.find(".pak") != -1:
				self.pakFiles.append(item)
		self.subDirs = sorted(self.subDirs)
		self.pakFiles = sorted(self.pakFiles)
		self.allFiles.extend(self.subDirs)
		self.allFiles.extend(self.pakFiles)
		for item in self.allFiles:
			self.pakList.addString(item)
		if self.name in self.allFiles:
			self.pakIdx = self.allFiles.index(self.name)
			self.pakList.selectString(self.name)
		elif self.pakIdx < len(self.allFiles):
			self.pakList.selectString(self.pakList.getStringForIndex(self.pakIdx))
		else:
			self.pakIdx = 0
			self.pakList.selectString(self.pakList.getStringForIndex(0))
		self.currentDirEditBox.setText(dialogOptions.currentDir)
		
	def inputCurrentDirEditBox(self, noeWnd, controlId, wParam, lParam):
		if self.currentDirEditBox.getText() != dialogOptions.currentDir and os.path.isdir(self.currentDirEditBox.getText()):
			dialogOptions.currentDir = self.currentDirEditBox.getText()
			self.setPakList()
			
	def inputGlobalScaleEditBox(self, noeWnd, controlId, wParam, lParam):
		global GlobalScale
		try:
			if self.globalScaleEditBox.getText():
				newScale = float(self.globalScaleEditBox.getText())
				if newScale:
					GlobalScale = newScale
		except ValueError:
			print("Non-numeric scale input, resetting to ", GlobalScale)
			self.globalScaleEditBox.setText(str(GlobalScale))
			
	def create(self, width=dialogOptions.width, height=dialogOptions.height):
		self.noeWnd = noewin.NoeUserWindow("Naughty Dog .pak Tool:        " + rapi.getLocalFileName(self.name), "HTRAWWindowClass", width, height) 
		noeWindowRect = noewin.getNoesisWindowRect()
		if noeWindowRect:
			windowMargin = 100
			self.noeWnd.x = noeWindowRect[0] + windowMargin
			self.noeWnd.y = noeWindowRect[1] + windowMargin  
		return self.noeWnd.createWindow()
	
	def createPakWindow(self, width=dialogOptions.width, height=dialogOptions.height):
		
		if self.create(width, height):
			self.noeWnd.setFont("Futura", 14)
			
			self.noeWnd.createStatic("Base:", 10, 7, width-20, 20)
			index = self.noeWnd.createComboBox(50, 5, width-65, 20, self.selectBaseListItem, noewin.CBS_DROPDOWNLIST) #CB
			self.baseList = self.noeWnd.getControlByIndex(index)
			
			self.noeWnd.createStatic("Files from:", 5, 45, width-20, 20)
			index = self.noeWnd.createEditBox(5, 65, width-20, 45, dialogOptions.currentDir, self.inputCurrentDirEditBox, False) #EB
			self.currentDirEditBox = self.noeWnd.getControlByIndex(index)
			
			#LBS_NOTIFY | WS_VSCROLL | WS_BORDER
			index = self.noeWnd.createListBox(5, 120, width-20, 380, self.selectPakListItem, noewin.LBS_NOTIFY | noewin.WS_VSCROLL | noewin.WS_BORDER) #LB
			self.pakList = self.noeWnd.getControlByIndex(index)
			
			self.noeWnd.createStatic("Files to load:", 5, 505, width-20, 20)
			index = self.noeWnd.createListBox(5, 525, width-20, 150, self.selectLoadListItem, noewin.CBS_DROPDOWNLIST) #LB
			self.loadList = self.noeWnd.getControlByIndex(index)
			
			
			if True:
				index = self.noeWnd.createCheckBox("Load Textures", 10, 685, 130, 30, self.checkLoadTexCheckbox)
				self.loadTexCheckbox = self.noeWnd.getControlByIndex(index)
				self.loadTexCheckbox.setChecked(dialogOptions.doLoadTex)
				
				
				index = self.noeWnd.createCheckBox("Load All Textures", 150, 685, 160, 30, self.checkLoadAllTexCheckbox)
				self.loadAllTexCheckbox = self.noeWnd.getControlByIndex(index)
				self.loadAllTexCheckbox.setChecked(dialogOptions.loadAllTextures)
				
				
				index = self.noeWnd.createCheckBox("Convert Textures", 10, 715, 130, 30, self.checkConvTexCheckbox)
				self.convTexCheckbox = self.noeWnd.getControlByIndex(index)
				self.convTexCheckbox.setChecked(dialogOptions.doConvertTex)
				
				
				#index = self.noeWnd.createCheckBox("Flip UVs", 140, 680, 130, 30, self.checkFlipUVsCheckbox)
				#self.flipUVsCheckbox = self.noeWnd.getControlByIndex(index)
				#self.flipUVsCheckbox.setChecked(dialogOptions.doFlipUVs)
				
				
				index = self.noeWnd.createCheckBox("Load Base", 150, 715, 90, 30, self.checkBaseCheckbox)
				self.loadBaseCheckbox = self.noeWnd.getControlByIndex(index)
				self.loadBaseCheckbox.setChecked(dialogOptions.doLoadBase)
				
				if ReparentHelpers == 2:
					dialogOptions.reparentHelpers = (dialogOptions.isTLOU2 or dialogOptions.isTLOUP1)
				index = self.noeWnd.createCheckBox("Reparent Helpers", 10, 750, 130, 20, self.checkReparentCheckbox)
				self.reparentCheckbox = self.noeWnd.getControlByIndex(index)
				self.reparentCheckbox.setChecked(dialogOptions.reparentHelpers)
				
				index = self.noeWnd.createCheckBox("Import LODs", 150, 745, 100, 30, self.checkLODsCheckbox)
				self.LODsCheckbox = self.noeWnd.getControlByIndex(index)
				self.LODsCheckbox.setChecked(dialogOptions.doLODs)
				

			self.noeWnd.createStatic("Game:", width-218, 690, 60, 20)
			index = self.noeWnd.createComboBox(width-170, 685, 150, 20, self.selectGameBoxItem, noewin.CBS_DROPDOWNLIST) #CB
			self.gameBox = self.noeWnd.getControlByIndex(index)
			
			self.noeWnd.createStatic("View:", width-210, 720, 60, 20)
			index = self.noeWnd.createComboBox(width-170, 715, 150, 20, self.selectLocalBoxItem, noewin.CBS_DROPDOWNLIST) #CB
			self.localBox = self.noeWnd.getControlByIndex(index)
			
			self.noeWnd.createStatic("Scale:", width-215, 750, 60, 20)
			index = self.noeWnd.createEditBox(width-170, 750, 80, 20, str(GlobalScale), self.inputGlobalScaleEditBox, False) #EB
			self.globalScaleEditBox = self.noeWnd.getControlByIndex(index)
			
			self.noeWnd.createButton("Load", 5, height-70, width-160, 30, self.openOptionsButtonLoadEntry)
			self.noeWnd.createButton("Cancel", width-96, height-70, 80, 30, self.openOptionsButtonCancel)
			
			self.setLoadList()
			self.setBaseList(self.baseList)
			self.setPakList()
			self.setGameBox(self.gameBox)
			self.setLocalBox(self.localBox)
			
			self.noeWnd.doModal()
			
TP1_pakStringIDs = {
	0x50CAF5257D6A140B: "JOINT_HIERARCHY",
	0x349D779A792F45C1: "GEOMETRY_1",
	0xCE3ADE693131B309: "VRAM_DESC",
	0xE7254422A7A8F476: "VRAM_DESC_TABLE",
	0xA2481DA1A5D2CE2B: "TEXTURE_TABLE",
	0x36125D3CFB7F3991: "TEXTURE_DICTIONARY",
	0x4903731234F1BEA6: "PAK_LOGIN_TABLE",
	0x61DE7E6141BC6F2B: "EFFECT_TABLE",
	0x460F497540A29F73: "SPAWNER_GROUP",
	0x596A72779C4C87D: "TAG_INT",
	0x53DE1E1977F9CBA4: "ANIM_GROUP",
	0x791137002DB17EBB: "MATERIAL_TABLE_1",
	0x384ADF724B123839: "FOREGROUND_SECTION_2",
	0x5ADB4A2D2E2A6EB: "COLLISION_DATA_CLOTH",
	0x3A3BB43D817C93DE: "TAG_VEC4",
	0x35EB8812D3A2D576: "TAG_FLOAT",
	0x6A98005088A56C5: "LEVEL_BOUNDING_BOX_DATA",
	0x438E1B0DBFFAA93: "AMBSHADOWS_OCCLUDER_INFO",
	0x7D9BFD5CEC879080: "COLLISION_DATA_FOREGROUND",
	0xEC3AFEDF7EF282F0: "SOUND_BANK_TABLE",
}

LODSubmeshDesc = namedtuple("LODSubmeshDesc", "name address offset index")

JointsInfo = namedtuple("JointsInfo", "transformsStart parentingStart")

StreamDesc = namedtuple("StreamDesc", "type offset stride bufferOffsetAddr")

T2StreamDesc = namedtuple("T2StreamDesc", "type offset stride bufferOffsetAddr sizes qScale qOffs numVerts")

SkinDesc = namedtuple("SkinDesc", "mapOffset weightsOffset weightCount mapOffsetAddr weightOffsetAddr uncompressed")

PakEntry = namedtuple("PakEntry", "type offset")

PakLoginTableEntry = namedtuple("PakLoginTableEntry", "page offset")

class PakSubmesh:
	def __init__(self, name=None, numVerts=None, numIndices=None, facesOffset=None, streamDescs=None, skinDesc=None, nrmRecalcDesc=None, streamsAddr=None, facesOffsetAddr=None, offset=None):
		self.name = name
		self.numVerts = numVerts
		self.numIndices = numIndices
		self.streamDescs = streamDescs
		self.skinDesc = skinDesc
		self.nrmRecalcDesc = nrmRecalcDesc
		self.facesOffset = facesOffset
		self.facesOffsetAddr = facesOffsetAddr
		self.bbox = []
		self.offset = offset

class PakFile:
	def __init__(self, bs, args=None):
		args = args or {}
		self.bs = bs
		self.pakPageEntries = []
		self.pointerPageIds = {}
		self.entriesList = []
		self.submeshes = []
		self.args = args
		self.path = args.get("path")
		self.texList = args.get("texList") or []
		self.matList = args.get("matList") or []
		self.matNames = args.get("matNames") or []
		self.vramHashes = args.get("vramHashes") or []
		self.userStreams = args.get("userStreams") or {}
		self.pakLoginTable = []
		self.lods = args.get("lods") or []
		self.xforms = {}
		self.jointsInfo = None
		self.jointOffset = None
		self.basePak = None
		self.geoOffset = None
		self.boneList = None
		self.boneMap = None
		self.boneDict = None
		self.doLODs = False
		self.needsBasePak = False
		self.animReader = None
		self.animClips = None
		self.meshPrefixes = tuple(args.get('meshPrefixes', ()))
		self.deformationRig = None
		self.geometryVersion = None
		self.submeshStride = 176
		self.submeshExtra = 0
		if args.get("doRead"):
			self.readPak()
		
	def getPointerFixupPage(self, readAddr):
		try:
			return self.pointerPageIds[readAddr][0]
		except:
			return None
		
	def changePointerFixup(self, address, newOffset, newPage):
		if address in self.pointerPageIds:
			returnAddr = self.bs.tell()
			writeUIntAt(self.bs, address, newOffset+20)
			self.bs.seek(self.pointerPageIds[address][1])
			self.bs.writeUShort(newPage)
			self.bs.seek(returnAddr)
		
	def readPointerFixup(self, TP1ZeroCondition=False):
		bs = self.bs
		readAddr = bs.tell()
		offset = bs.readInt64()
		if offset > 0 or TP1ZeroCondition:
			pageId = self.getPointerFixupPage(readAddr)
			if pageId != None:
				return offset + self.pakPageEntries[pageId][0]
			if not TP1ZeroCondition:
				raise ValueError("pointer fixup is missing at file offset %d" % readAddr)
			
		return offset
	
	def loadBaseSkeleton(self, skelPath):
		if skelPath and rapi.checkFileExists(skelPath):
			self.basePak = PakFile(NoeBitStream(rapi.loadIntoByteArray(skelPath)), {'path':skelPath})
			self.basePak.readPak()
			self.boneList = self.basePak.boneList
			self.boneMap = self.basePak.boneMap
			return 1
		else:
			print("\nERROR: Failed to load base skeleton from", skelPath, "\n")
			return 0
	
	def makeVramHashJson(self, jsons):
		fileName = rapi.getLocalFileName(self.path)
		jsons[fileName] = {}
		for vramHash, subTuple in self.vrams.items():
			jsons[fileName][str(vramHash)] = subTuple[0]
			
	def dumpGlobalVramHashes(self):
		"""Rebuild derived texture indexes from the configured local game files."""
		global ResItemPaddingSz
		gameDir = BaseDirectories[gameName]
		if not gameDir or not os.path.isdir(gameDir):
			raise ValueError("configure a valid base directory before rebuilding texture indexes")
		cachePath = os.path.join(noesis.getPluginsPath(), "python", "NDTextureHashes.json")
		jsons = loadTextureHashCache()
		modern = gameName in ("TLOUP1", "TLOU2")
		if modern:
			directories = [(name, os.path.join(gameDir, name, "texturedict3"))
				for name in sorted(os.listdir(gameDir))
				if os.path.isdir(os.path.join(gameDir, name, "texturedict3"))]
		else:
			directories = [("All", os.path.join(gameDir, "texturedict2"))]
		if not directories or any(not os.path.isdir(path) for world, path in directories):
			raise ValueError("no texture dictionary directory exists under the configured game root")
		gameHashes = {}
		gameOffsets = {}
		indexed = 0
		textureRecords = 0
		metadataBytes = 0
		layout = (dialogOptions.isTLOUP1, dialogOptions.isTLOU2, ResItemPaddingSz)
		try:
			for world, directory in directories:
				hashes = {} if modern else gameHashes
				offsets = {}
				for fileName in sorted(os.listdir(directory)):
					if "-dict" not in fileName.lower() or not fileName.lower().endswith(".pak"):
						continue
					path = os.path.join(directory, fileName)
					if not os.path.isfile(path):
						continue
					metadata = readTextureDictionaryMetadata(path)
					dictPak = PakFile(NoeBitStream(metadata), {"path": path})
					dictPak.readPakHeader()
					if not dictPak.pakPageEntries:
						raise ValueError("could not parse texture dictionary: " + path)
					page = dictPak.pakPageEntries[-1]
					offsets[fileName] = page[0] + page[1]
					dictPak.makeVramHashJson(hashes)
					indexed += 1
					textureRecords += len(dictPak.vrams)
					metadataBytes += len(metadata)
				if modern:
					gameHashes[world] = hashes
				gameOffsets[world] = offsets
		finally:
			dialogOptions.isTLOUP1, dialogOptions.isTLOU2, ResItemPaddingSz = layout
		if not indexed or not textureRecords:
			raise ValueError("no texture records found in the configured texture dictionaries")
		jsons[gameName] = gameHashes
		offsetsByGame = dict(gdRawDataStarts)
		offsetsByGame[gameName] = gameOffsets
		with open(cachePath, "w") as destination:
			json.dump(jsons, destination)
		_saveRawDataStarts(offsetsByGame)
		gdRawDataStarts[gameName] = gameOffsets
		dialogOptions.texDicts = jsons
		print("[NDTextures] rebuilt %d dictionary indexes for %s (%d texture records, %d metadata bytes)"
			% (indexed, gameName, textureRecords, metadataBytes))

	def ensureTextureIndex(self):
		self.texDict = (dialogOptions.texDicts or {}).get(gameName) or {}
		if self.texDict:
			return
		cacheKey = (gameName, BaseDirectories[gameName])
		if cacheKey in dialogOptions.textureIndexAttempts:
			return
		dialogOptions.textureIndexAttempts.add(cacheKey)
		print("[NDTextures] no texture index for %s; building from local dictionaries" % gameName)
		try:
			self.dumpGlobalVramHashes()
		except Exception as error:
			print("[NDTextures] high-resolution index unavailable; using local textures: %s: %s"
				% (type(error).__name__, error))
			return
		self.texDict = dialogOptions.texDicts[gameName]
	
	def writeVRAMImage(self, vramOffset, filepath):
		
		if rapi.checkFileExists(filepath):
			bs = self.bs
			vramOffset = vramOffset or bs.tell()
			offset = readUIntAt(bs, vramOffset+40)
			vramSize = readUIntAt(bs, vramOffset+48)
			imgFormat = readUIntAt(bs, vramOffset+72)
			fmtName = dxFormat.get(imgFormat) or ""
			rawDataStart = self.pakPageEntries[len(self.pakPageEntries)-1][0] + self.pakPageEntries[len(self.pakPageEntries)-1][1]
			newDataOffset = offset + rawDataStart
			
			ds = NoeBitStream(rapi.loadIntoByteArray(filepath))
			if filepath.count(".tga"):
				#fetch tga data
				ds.seek(12)
				width = ds.readUShort()
				height = ds.readUShort()
				depth = ds.readUByte()
				ds.seek(18)
				imgBytes = ds.readBytes(ds.getSize() - 18)
				imgBytes, numMips = encodeImageData(imgBytes, width, height, fmtName)
			elif filepath.count(".dds"):
				#fetch dds data
				magic = ds.readUInt()
				hdrSize = ds.readUInt()
				flags = ds.readUInt()
				height = ds.readUInt()
				width = ds.readUInt()
				pitchOrLinearSize = ds.readUInt()
				depth = ds.readUInt()
				numMips = ds.readUInt()
				isDX10 = (readUIntAt(ds, 84) == 808540228)
				ds.seek(hdrSize+4)
				if isDX10:
					compressionType = ds.readUInt() 
					ds.seek(16, 1) #skip DX10 header
				imgBytes = ds.readBytes(ds.getSize() - ds.tell())
				
			if dialogOptions.isTLOU2:
				imgBytes = rapi.callExtensionMethod("tile_1dthin", imgBytes, width, height, 4 if (fmtName.count("Bc1") or fmtName.count("Bc4")) else 8, 1)
				if noesis.optWasInvoked("-t"):
					numMips = 1
			
			if True: #len(imgBytes) > vramSize: #NEEDS FIXING
				newDataOffset = bs.getSize()
				bs.seek(32)
				bs.writeUInt(readUIntAt(bs, bs.tell())+len(imgBytes)) #added size to raw_data
			
			bs.seek(vramOffset+40)
			bs.writeUInt(newDataOffset - rawDataStart) #new offset
			bs.seek(vramOffset+48)
			bs.writeUInt(len(imgBytes)) #new size
			bs.seek(vramOffset+84)
			bs.writeUInt(width) #new width
			bs.seek(vramOffset+88)
			bs.writeUInt(height) #new height
			bs.seek(vramOffset+80)
			bs.writeUInt(numMips) #new mips
			
			#replace hashes
			bs.seek(vramOffset+56)
			hashOld = bs.readBytes(8)
			bs.seek(-8, 1)
			hashNew = struct.pack('<Q', bs.readUInt64() + 1)
			
			bs.seek(0)
			searchBytes = bs.readBytes(rawDataStart)
			bs.seek(0)
			bs.writeBytes(searchBytes.replace(hashOld, hashNew))
			
			#write image data
			bs.seek(newDataOffset)
			bs.writeBytes(imgBytes)
			
			return 1
			
		else:
			print("Texture not found:", filepath)
	
	def loadVRAM(self, vramOffset=0, exTexName=""):
		
		if dialogOptions.doConvertTex and exTexName.find("NoesisBrown") != -1: 
			return generateDummyTexture4px([32, 26, 18, 255], exTexName)
		elif dialogOptions.doConvertTex and exTexName.find("NoesisGray") != -1: 
			return generateDummyTexture4px([127, 127, 127, 255], exTexName)
		elif dialogOptions.doConvertTex and exTexName.find("NoesisWhite") != -1: 
			return generateDummyTexture4px([255, 255, 255, 255], exTexName)
		elif dialogOptions.doConvertTex and exTexName.find("NoesisNRM") != -1: 
			return generateDummyTexture4px([127, 127, 254, 255], exTexName)

		
		bs = self.bs
		vramOffset = vramOffset or bs.tell()
		bs.seek(vramOffset + 40)
		pakOffset = bs.readUInt()
		unknown0 = bs.readUInt()
		vramSize = bs.readUInt()
		textureDictId = bs.readUInt()
		m_hash = bs.readUInt64()
		unknown1 = bs.readUInt()
		m_type = bs.readUInt()
		imgFormat = bs.readUInt()
		field_2C = bs.readUInt()
		m_mipCount = bs.readUInt()
		width = bs.readUInt()
		height = bs.readUInt()
		field_3C = bs.readUInt()
		m_streamFlags = bs.readUInt()
		texFileName = self.vrams[m_hash][1]
		texPath = readStringAt(bs, bs.tell()+12)
		
		bigVramOffset = None
		bigVramDictFile = ""
		worldName = "All"
		
		if gameName == "TLOU2" or gameName == "TLOUP1":
			for worldFolderName, worldDict in self.texDict.items():
				for fileName, subDict in worldDict.items(): 
					bigVramOffset = subDict.get(str(m_hash))
					if bigVramOffset: 
						if rapi.checkFileExists(BaseDirectories[gameName] + worldFolderName + "\\texturedict3\\" + fileName):
							bigVramDictFile = BaseDirectories[gameName] + worldFolderName + "\\texturedict3\\" + fileName
							worldName = worldFolderName
							break
						else:
							bigVramOffset = None
							print("Texture hash was found, but Texture Dict does not exist!\n	", BaseDirectories[gameName] + worldFolderName + "\\texturedict3\\" + fileName)
				if bigVramOffset: break
		else:
			for fileName, subDict in self.texDict.items():
				bigVramOffset = subDict.get(str(m_hash))
				if bigVramOffset: 
					if rapi.checkFileExists(BaseDirectories[gameName] + "texturedict2\\" + fileName):
						bigVramDictFile = BaseDirectories[gameName] + "texturedict2\\" + fileName
						break
					else:
						bigVramOffset = None
						print("Texture hash was found, but Texture Dict does not exist!", texFileName, "\n	", BaseDirectories[gameName] + "texturedict2\\" + fileName)
		
		if bigVramOffset: 
			vramBytes = readFileBytes(bigVramDictFile, bigVramOffset, 1024)
			vramStream = NoeBitStream(vramBytes)
			offset = readUIntAt(vramStream, 40)
			width = readUIntAt(vramStream, 84)
			height = readUIntAt(vramStream, 88)
			vramSize = readUIntAt(vramStream, 48)
			imgFormat = readUIntAt(vramStream, 72)
			# dynamically compute rawDataStart from the dict pak header (last page offset + size)
			# and persist to NDRawDataStarts.json so it is never hardcoded
			if fileName not in gdRawDataStarts[gameName].get(worldName, {}):
				with open(bigVramDictFile, 'rb') as _f:
					import struct as _struct
					_f.seek(16)
					_pageCt = _struct.unpack('<I', _f.read(4))[0]
					_pageTablePtr = _struct.unpack('<I', _f.read(4))[0]
					_f.seek(_pageTablePtr + 12*(_pageCt-1))
					_pgOff = _struct.unpack('<I', _f.read(4))[0]
					_pgSz  = _struct.unpack('<I', _f.read(4))[0]
				gdRawDataStarts[gameName] = gdRawDataStarts.get(gameName) or {}
				gdRawDataStarts[gameName][worldName] = gdRawDataStarts[gameName].get(worldName) or {}
				gdRawDataStarts[gameName][worldName][fileName] = _pgOff + _pgSz
				_saveRawDataStarts(gdRawDataStarts)
				print("NDRawDataStarts.json updated:", gameName, worldName, fileName, "=", _pgOff + _pgSz)
			rawDataStart = gdRawDataStarts[gameName][worldName][fileName]
			print("VRAM texture hash found!", fileName, '{:02X}'.format(m_hash), texFileName,
			  "  offset=", offset,
			  "  rawDataStart=", rawDataStart,
			  "  finalSeek=", offset + rawDataStart,
			  "  vramSize=", vramSize,
			  "  imgFormat=", imgFormat,
			  "  WxH=", width, height)
			imageData = readFileBytes(bigVramDictFile, offset + rawDataStart, vramSize)
		else:
			print("Loading local texture", texFileName, "  WxH=", width, height)
			bs.seek(pakOffset + self.pakPageEntries[len(self.pakPageEntries)-1][0] + self.pakPageEntries[len(self.pakPageEntries)-1][1])
			imageData = bs.readBytes(vramSize)

		fmtName = dxFormat.get(imgFormat) or ""
		bpp = 4 if (fmtName.count("Bc1") or fmtName.count("Bc4")) else 8
		
		if dialogOptions.isTLOU2:
			imageData = rapi.callExtensionMethod("untile_1dthin", imageData, width, height, bpp, 1)
		
		decodeFmt, encodeFmt, bpp = getDXTFormat(fmtName)
		
		if isinstance(decodeFmt, str):
			print("RGBA: ", fmtName)
			try:
				texData = rapi.imageDecodeRaw(imageData, width, height, decodeFmt)
			except:
				print("Failed to decode raw image type", fmtName)
		elif decodeFmt != None:
			texData = rapi.imageDecodeDXT(imageData, width, height, decodeFmt)
			if dialogOptions.doConvertTex and decodeFmt == noesis.FOURCC_BC7: 
				if exTexName.count("_NoesisAO"):
					texData = rapi.imageEncodeRaw(texData, width, height, "r8r8r8")
					texData = rapi.imageDecodeRaw(texData, width, height, "r8g8b8")
					texFileName = exTexName
				elif texFileName.count("-ao") or texFileName.count("-occlusion"):
					texData = rapi.imageEncodeRaw(texData, width, height, "g16b16")
					texData = rapi.imageDecodeRaw(texData, width, height, "r16g16")
		else:
			print("Error: Unsupported texture type: " + str(imgFormat) + "  " + fmtName)
			
		return NoeTexture(texFileName, width, height, texData, noesis.NOESISTEX_RGBA32)
	
	def checkResItem(self, start, m_resItemOffset, m_itemType):
		bs = self.bs
		self.entriesList.append(PakEntry(type=m_itemType, offset = m_resItemOffset))
		
		if m_itemType == "VRAM_DESC":
			if dialogOptions.isTLOU2:
				m_resItemOffset += 16
			bs.seek(m_resItemOffset + start + 56)
			texHash = bs.readUInt64()
			texPath = readStringAt(bs, m_resItemOffset + start + 112)
			delimiter = ".exr/" if ".exr/" in texPath else ".tga/"
			splitted = rapi.getLocalFileName(texPath.replace(delimiter, "+")).split("+", 1)
			texName = splitted[0] + texoutExt
			if len(splitted) > 1:
				if texName in self.vramNames:
					texName = (splitted[0] + "_" + splitted[1]).replace(".ndb", texoutExt) #add hash to duplicate texture names
				self.vramNames[texName] = True
				self.vrams[texHash] = [m_resItemOffset + start, texName, [], None]
		
		if m_itemType == "JOINT_HIERARCHY":
			self.jointOffset = (m_resItemOffset, start)
			
		if m_itemType == "GEOMETRY_1":
			self.geoOffset = (m_resItemOffset, start)
			self.geometryVersion = readUIntAt(bs, start + m_resItemOffset + ResItemPaddingSz)
			modernGeometry = dialogOptions.isTLOU2 or dialogOptions.isTLOUP1
			self.submeshStride, self.submeshExtra = geometrySubmeshLayout(self.geometryVersion, modernGeometry)
			m_numSubMeshDesc = readUIntAt(bs, self.geoOffset[0] + self.geoOffset[1] + ResItemPaddingSz + 8)
			bs.seek(self.geoOffset[0] + self.geoOffset[1] + ResItemPaddingSz + 40)
			SubmeshesOffs = self.readPointerFixup()
			for i in range(m_numSubMeshDesc):
				bs.seek(SubmeshesOffs + self.submeshStride*i + (88 if modernGeometry else 104))
				self.needsBasePak = self.needsBasePak or not not bs.readUInt64()
	
	def readPakHeader(self):
	
		global dialogOptions, ResItemPaddingSz
		
		print ("Reading", self.path or rapi.getInputName())
		readPointerFixup = self.readPointerFixup
		
		bs = self.bs
		bs.seek(0)
		m_magic = bs.readUInt()						#0x0 0x00000A79
		if m_magic != 2681 and m_magic != 68217 and m_magic != 2147486329 and m_magic != 2685 and m_magic != 68221:
			print("No pak header detected!", m_magic)
			return 0
		dialogOptions.isTLOUP1 = (m_magic == 2685 or m_magic == 68221)
		
		m_hdrSize = bs.readUInt()					#0x4 header size
		m_pakLoginTableIdx = bs.readUInt()			#0x8 idx of the page storing the PakLoginTable
		m_pakLoginTableOffset = bs.readUInt()		#0xC relative offset PakLoginTable = PakPageHeader + m_pakLoginTableOffset; //its a ResItem
		m_pageCt = bs.readUInt()					#0x10 page count. Total number of pages in the package
		m_pPakPageEntryTable = bs.readUInt()		#0x14 ptr to the PakPageEntry array/table
		m_numPointerFixUpPages = bs.readUInt()		#0x18 always 0x8
		m_pointerFixUpTableOffset = bs.readUInt()	#0x1C ptr to the PointerFixUpTable table
		m_unk5 = bs.readUInt()						#0x20 no idea
		m_unk6 = bs.readUInt()						#0x20 no idea
		m_unk7 = bs.readUInt()						#0x20 no idea
		if dialogOptions.isTLOUP1:
			m_unk8 = bs.readUInt()
			m_unk9 = bs.readUInt()
			m_unk10 = bs.readUInt()
			
		bs.seek(m_pPakPageEntryTable)
		self.pakPageEntries = []
		for i in range(m_pageCt):
			self.pakPageEntries.append((bs.readUInt(), bs.readUInt(), bs.readUInt()))
		
		bs.seek(m_pointerFixUpTableOffset)
		m_pageEntryNumber = bs.readUInt()
		m_dataOffset = bs.readUInt()
		m_numLoginPageEntries = bs.readUInt()
		bs.seek(m_dataOffset)
		
		self.pointerPageIds = {}
		for i in range(m_numLoginPageEntries):
			m_page1Idx = bs.readUShort()
			m_page2Idx = bs.readUShort()
			pointerOffs = bs.readUInt()
			self.pointerPageIds[pointerOffs + self.pakPageEntries[m_page1Idx][0]] = (m_page2Idx, bs.tell()-6)
			
		self.jointOffset = self.geoOffset = None
		self.vrams = {}
		
		if dialogOptions.texDicts is None:
			dialogOptions.texDicts = loadTextureHashCache()
		
		for name in gamesList:
			if not isinstance(dialogOptions.texDicts.get(name), dict):
				dialogOptions.texDicts[name] = {}
		self.texDict = dialogOptions.texDicts[gameName]

		self.pakLoginTable = []
		self.vramNames = {}
		
		pakLoginTableItemStart = self.pakPageEntries[m_pakLoginTableIdx][0] + m_pakLoginTableOffset
		dialogOptions.isTLOU2 = (readUIntAt(bs, pakLoginTableItemStart+32) == 74565)
		ResItemPaddingSz = 48 if (dialogOptions.isTLOU2 or dialogOptions.isTLOUP1) else 32
		bs.seek(pakLoginTableItemStart + ResItemPaddingSz)
		loginCount = bs.readUInt()
		bs.seek(4, 1)
		
		for i in range(loginCount):
			self.pakLoginTable.append(PakLoginTableEntry(page=bs.readUInt(), offset=bs.readUInt()))
		
		if dialogOptions.isTLOUP1: #the outer pak format was changed a lot for TLOU Part I. ResPage and ResPageEntry are gone, now all ResItems are accessed from the pak login table and have StringIDs for names
			for loginResItem in self.pakLoginTable:
				start = self.pakPageEntries[loginResItem.page][0]
				bs.seek(start + loginResItem.offset + 32)
				typeStringID = bs.readUInt64()
				m_itemType = TP1_pakStringIDs.get(typeStringID)
				if m_itemType:
					self.checkResItem(start, loginResItem.offset, m_itemType)
					if m_itemType == "TEXTURE_TABLE" or m_itemType == "TEXTURE_DICTIONARY":
						numTex = readUIntAt(bs, start + loginResItem.offset + ResItemPaddingSz)
						bs.seek(start + loginResItem.offset + ResItemPaddingSz + 24)
						listStart = readPointerFixup()
						for i in range(numTex):
							bs.seek(listStart+i*8)
							pageID = self.getPointerFixupPage(bs.tell())
							pointer = bs.readUInt64()
							self.checkResItem(self.pakPageEntries[pageID][0], pointer-32, "VRAM_DESC")
		else:
			for p, pageEntry in enumerate(self.pakPageEntries):
				start = pageEntry[0]
				bs.seek(start + 12)
				m_pageSize = bs.readUInt()
				bs.seek(2,1)
				m_numPageHeaderEntries = bs.readUShort()
				
				for ph in range(m_numPageHeaderEntries):
					m_name = readStringAt(bs, bs.readUInt64()+start)
					m_resItemOffset = bs.readUInt()
					place = bs.tell() + 4
					bs.seek(m_resItemOffset + start)
					m_itemNameOffset = bs.readUInt64()
					m_itemName = readStringAt(bs, m_itemNameOffset+start)
					m_itemTypeOffset = bs.readUInt64()
					m_itemType = readStringAt(bs, m_itemTypeOffset+start)
					self.checkResItem(start, m_resItemOffset, m_itemType)
					bs.seek(place)
		
	def readPak(self):
		
		global dialogOptions
		bs = self.bs
		readPointerFixup = self.readPointerFixup
		
		if len(self.pakPageEntries) == 0:
			self.readPakHeader()
		start = self.pakPageEntries[0]
		
		if not self.jointOffset and dialogOptions.doLoadBase and dialogOptions.baseSkeleton: # and dialogOptions.baseIdx != -1:
			localRoot = findRootDir(rapi.getOutputName() or rapi.getInputName())
			baseSkelPath = BaseDirectories[gameName] + dialogOptions.baseSkeleton if dialogOptions.baseSkeleton[1] != ":" else dialogOptions.baseSkeleton
			if rapi.checkFileExists(localRoot + dialogOptions.baseSkeleton):
				baseSkelPath = localRoot + dialogOptions.baseSkeleton
				if rapi.checkFileExists(baseSkelPath.replace(".pak", ".NEW.pak")): 
					baseSkelPath = baseSkelPath.replace(".pak", ".NEW.pak")
				print("\nFound local base pak: ", baseSkelPath, "\n")
			dialogOptions.baseSkeleton = ""
			if not self.loadBaseSkeleton(baseSkelPath) and not noesis.optWasInvoked("-t"):
				return 0
		
		if self.jointOffset:
			start = self.jointOffset[1]
			print("Found Joint Hierarchy") # offset", self.jointOffset[0] + start, ", location:", self.jointOffset[0] + start + 20 + 32)
			bs.seek(self.jointOffset[0] + start + 20 + ResItemPaddingSz)
			boneCount = bs.readUInt()
			bs.seek(8,1)
			xformsOffset = readPointerFixup()
			flagsOffset = bs.readUInt64()
			uknOffset = bs.readUInt64()
			namesOffset = readPointerFixup()
			
			bs.seek(xformsOffset + 16)
			nodeCount = bs.readUShort()
			xformCount = bs.readUShort()
			uknCount = bs.readUShort()
			uknShort = bs.readUShort()
			uknHash0 = bs.readUInt()
			uknHash1 = bs.readUInt()
			headerSize = bs.readUInt()
			uknInt0 = bs.readUInt()
			uknInt1 = bs.readUInt()
			aOffs = bs.readUInt()
			bOffs = bs.readUInt()
			cOffs = bs.readUInt()
			uknInt2 = bs.readUInt()
			hierarchyOffset = bs.readUInt()
			
			self.boneList = self.boneList or []
			parentList = []
			matrixList = []
			
			transformsStart = xformsOffset + headerSize
			bs.seek(transformsStart)
			for b in range(xformCount):
				scale = NoeVec3((bs.readFloat(), bs.readFloat(), bs.readFloat()))
				bs.seek(4,1)
				rotation = NoeQuat((bs.readFloat(), bs.readFloat(), bs.readFloat(), bs.readFloat())).transpose()
				position = NoeVec3((bs.readFloat(), bs.readFloat(), bs.readFloat()))
				bs.seek(4,1)
				mat = rotation.toMat43()
				mat[3] = position * GlobalScale
				matrixList.append(mat)
				
			bs.seek(xformsOffset + hierarchyOffset + 20)
			hashesSize = bs.readUInt()
			bs.seek(hashesSize - 24, 1)
			parentingStart = bs.tell()
			for b in range(boneCount):
				parentList.append((bs.readInt(), bs.readInt(), bs.readInt(), bs.readInt())) #GroupID, ParentID, ChildID, ChainID
			
			self.jointsInfo = JointsInfo(transformsStart=transformsStart, parentingStart=parentingStart)
			
			
			mainBoneMats = []
			boneNames = []
			self.boneDict = []
			self.boneMap = []
			oldBoneNames = [bone.name for bone in self.boneList]
			
			bs.seek(namesOffset)
			for b in range(boneCount):
				bs.seek(8,1)
				boneNames.append(readStringAt(bs, bs.readUInt64()+start))
				
			def getRootParent(parentTbl):
				while parentTbl[1] != -1:
					parentTbl = parentList[parentTbl[1]]
				return parentList.index(parentTbl)
			
			for b in range(boneCount):	
				if getRootParent(parentList[b]) == 0:
					self.boneMap.append(b)
				
			identity = NoeMat43((NoeVec3((1.0, 0.0, 0.0)), NoeVec3((0.0, 1.0, 0.0)), NoeVec3((0.0, 0.0, 1.0)), NoeVec3((0.0, 0.0, 0.0))))
			startBoneIdx = len(self.boneList)
			for b, bID in enumerate(self.boneMap):
				mat = matrixList[b] if b < len(matrixList) else identity
				mainBoneMats.append(mat)
			
			for b in range(boneCount):
				
				if b in self.boneMap:
					self.boneList.append(NoeBone(startBoneIdx + b, boneNames[b], mainBoneMats[self.boneMap.index(b)], None, parentList[b][1]))
				else:
					splitted = boneNames[b].split("_")
					endName = splitted[len(splitted)-1]
					matchedName = boneNames[b].replace("_" + endName, "")#.replace("_runtime", "")
					bFound = False
					mat = identity
					if parentList[b][3] != -1 and parentList[b][3] in self.boneMap: #parentList[b][3] < len(self.boneMap) and self.boneMap[parentList[b][3]] < len(mainBoneMats):
						mat = mainBoneMats[self.boneMap[parentList[b][3]]]
					for j, bId in enumerate(self.boneMap):
						if boneNames[bId].find(matchedName) != -1:
							bFound = True
							self.boneList.append(NoeBone(startBoneIdx + b, boneNames[b], mat, None, parentList[b][1]))
							if parentList[b][1] == -1 or (dialogOptions.reparentHelpers and (endName == "helper" or endName == "grp")): # or endName == "mover"
								self.boneList[len(self.boneList)-1].parentIndex = bId
							break
					if not bFound:
						self.boneList.append(NoeBone(startBoneIdx + b, boneNames[b], mat, None, parentList[b][1]))
				lastBone = self.boneList[len(self.boneList)-1]
				if lastBone.parentIndex != -1:
					lastBone.parentIndex += startBoneIdx
				elif b not in self.boneMap:
					if lastBone.name == "eyelash_grp" and "headb" in boneNames:
						lastBone.parentIndex = boneNames.index("headb") + startBoneIdx
					else:	
						lastBone.parentIndex = 0 #parent stragglers to root
			if dialogOptions.isTLOUP1 and not noesis.optWasInvoked('-ndlegacyhelpers'):
				self.deformationRig = inc_nd_skeleton.readDeformationRig(
					bs.getBuffer(), xformsOffset, boneNames,
					[entry[1] for entry in parentList], self.boneMap)
				if self.deformationRig:
					for boneIndex, matrix in enumerate(self.deformationRig.localBind):
						bone = self.boneList[startBoneIdx + boneIndex]
						nativeMatrix = NoeMat43((
							NoeVec3((matrix[0], matrix[1], matrix[2])),
							NoeVec3((matrix[4], matrix[5], matrix[6])),
							NoeVec3((matrix[8], matrix[9], matrix[10])),
							NoeVec3((matrix[3] * GlobalScale, matrix[7] * GlobalScale, matrix[11] * GlobalScale))))
						if boneIndex in self.deformationRig.mainBones:
							previousMatrix = bone.getMatrix()
							if max(abs(nativeMatrix[row][axis] - previousMatrix[row][axis])
									for row in range(4) for axis in range(3)) > 0.001:
								raise ValueError('native bind convention does not match animation joint: ' + bone.name)
						bone.setMatrix(nativeMatrix)
						parent = self.deformationRig.parents[boneIndex]
						bone.parentIndex = startBoneIdx + parent if parent >= 0 else -1
					print('[NDRig] native inverse binds: %d bones; bind-space followers: %d; compatibility constraints: %d' %
						(boneCount, len(self.deformationRig.followers), len(self.deformationRig.constraints)))
						
		if self.geoOffset:
			start = self.geoOffset[1]
			print("Found Geometry") # offset", self.geoOffset[0] + start)
			
			lastLOD = 0
			self.submeshes = []
			
			bs.seek(self.geoOffset[0] + start + ResItemPaddingSz)
			
			m_version = bs.readUInt()
			m_isForeground = bs.readUInt()
			m_numSubMeshDesc = bs.readUInt()
			m_numLODs = bs.readUInt()
			m_numMaterials = bs.readUInt()
			m_unk4 = bs.readUInt()
			m_numShaders = bs.readUInt()
			m_unk6 = bs.readUInt()
			m_unk7 = bs.readUInt()
			m_unk8 = bs.readUInt()
			SubmeshesOffs = readPointerFixup()
			
			self.xforms = {}
			if dialogOptions.isTLOU2 or dialogOptions.isTLOUP1:
				uknStruct = readPointerFixup()
				m_papTransform = readPointerFixup()
				textureDescsOffs = readPointerFixup()
				shaderDescsOffs = readPointerFixup()
				materialDescsOffs = readPointerFixup()
			else:
				LODDescsOffs = readPointerFixup()
				ukn0 = bs.readUInt64()
				textureDescsOffs = readPointerFixup()
				shaderDescsOffs = readPointerFixup()
				ukn3 = bs.readUInt64()
				uknFloatsOffs = readPointerFixup()
				materialDescsOffs = readPointerFixup()
			
			if 'm_papTransform' in locals():
				for i in range(m_numMaterials):
					bs.seek(m_papTransform + 8*i)
					offset = readPointerFixup()
					bs.seek(offset)
					mat = NoeMat44((NoeVec4((bs.readFloat(), bs.readFloat(), bs.readFloat(), bs.readFloat())), NoeVec4((bs.readFloat(), bs.readFloat(), bs.readFloat(), bs.readFloat())), NoeVec4((bs.readFloat(), bs.readFloat(), bs.readFloat(), bs.readFloat())), NoeVec4((bs.readFloat(), bs.readFloat(), bs.readFloat(), bs.readFloat())))).toMat43()
					#vec4_1 = NoeQuat((bs.readFloat(), bs.readFloat(), bs.readFloat(), bs.readFloat()))
					#vec4_2 = NoeVec4((bs.readFloat(), bs.readFloat(), bs.readFloat(), bs.readFloat())).toVec3()
					#mat *= vec4_1.toMat43()
					#mat[3] += vec4_2
					bs.seek(offset + 152)
					submeshXformsOffs = readPointerFixup()
					numSubmeshXforms = readUIntAt(bs, offset+212)
					for j in range(numSubmeshXforms):
						bs.seek(submeshXformsOffs + j*112 + 64)
						submeshOffs = readPointerFixup(True)
						self.xforms[submeshOffs] = self.xforms.get(submeshOffs) or []
						self.xforms[submeshOffs].append(mat)
			
			usedMaterials = {}
			usedTextures = []
			for i in range(m_numSubMeshDesc):
				bs.seek(SubmeshesOffs + self.submeshStride*i)
				if dialogOptions.isTLOU2 or dialogOptions.isTLOUP1:
					bbox = [[bs.readFloat(), bs.readFloat(), bs.readFloat(), bs.readFloat()], [bs.readFloat(), bs.readFloat(), bs.readFloat(), bs.readFloat()]]
					submeshName = readStringAt(bs, readPointerFixup() or start).split("|")
					submeshName = submeshName[len(submeshName)-1]
					ukn64_0 = bs.readUInt64()
					m_pStreamDesc = readPointerFixup()
					ukn64_1 = bs.readUInt64()
					facesOffsetAddr = bs.tell()
					m_pIndexes = readPointerFixup(True)
					m_material = readPointerFixup()
					ukn64_2 = bs.readUInt64()
					skindataOffset = readPointerFixup()
					ukn64_3 = bs.readUInt64()
					ukn64_4 = bs.readUInt64()
					nrmRecalcDescOffsOffset = bs.tell()
					nrmRecalcDescOffs = readPointerFixup()
					uknStringOffs = bs.readUInt64()
					bs.seek(self.submeshExtra, 1)
					m_numVertexes = bs.readUInt()
					m_numIndexes = bs.readUInt()
					m_numStreamSource = bs.readUInt()
					m_numDefaultStreams = bs.readUInt()
					ukn32_0 = bs.readUInt()
					ukn32_1 = bs.readUInt()
					ukn64_5 = bs.readUInt64()
					ukn32_2 = bs.readUInt()
					ukn32_3 = bs.readUInt()
					ukn32_4 = bs.readUInt()
					ukn32_5 = bs.readUInt()
				else:
					field_0 = bs.readUInt()
					field_4 = bs.readUInt()
					submeshName = readStringAt(bs, readPointerFixup()).split("|")
					submeshName = submeshName[len(submeshName)-1]
					field_10 = bs.readUInt()
					field_14 = bs.readUInt()
					field_18 = bs.readUInt()
					field_1C = bs.readUInt()
					field_20 = bs.readUInt()
					m_numVertexes = bs.readUInt()
					m_numIndexes = bs.readUInt()
					m_numStreamSource = bs.readUInt()
					m_numDefaultStreams = bs.readInt()
					field_34 = bs.readUInt()
					m_pStreamDesc = readPointerFixup()
					field_40 = bs.readUInt()
					field_44 = bs.readUInt()
					facesOffsetAddr = bs.tell()
					m_pIndexes = readPointerFixup()
					m_material = readPointerFixup()
					m_numMaterialInstances = bs.readUInt()
					field_5C = bs.readUInt()
					field_60 = bs.readUInt()
					field_64 = bs.readUInt()
					skindataOffset = readPointerFixup()
					field_70 = bs.readUInt()
					field_74 = bs.readUInt()
					field_78 = bs.readUInt()
					field_7C = bs.readUInt()
					field_80 = bs.readUInt()
					field_84 = bs.readUInt()
					nrmRecalcDescOffsOffset = bs.tell()
					nrmRecalcDescOffs = readPointerFixup()
					field_90 = bs.readUInt()
					field_94 = bs.readUInt()
					field_98 = bs.readUInt()
					field_9C = bs.readUInt()
					field_A0 = bs.readUInt()
					field_A4 = bs.readUInt()
					field_A8 = bs.readUInt()
					field_AC = bs.readUInt()
				
				place = bs.tell()
				streamDescs = []
				
				for j in range(m_numStreamSource):
					
					if dialogOptions.isTLOU2 or dialogOptions.isTLOUP1:
						bs.seek(m_pStreamDesc + 64*j)
						buffOffsAddr = bs.tell()
						m_bufferOffset = readPointerFixup(True)
						
						numVerts = bs.readUInt()
						uknInt = bs.readUInt()
						bufferSize = bs.readUInt()
						
						m_compType = bs.readUByte()
						m_unk2 = bs.readUByte()
						m_unk3 = bs.readBits(4)
						m_stride = bs.readBits(4)
						m_unk4 = bs.readUByte()
						sizes = [bs.readUByte(), bs.readUByte(), bs.readUByte(), bs.readUByte()]
						
						uknInt0 = bs.readUInt()
						qScale = NoeVec4((bs.readFloat(), bs.readFloat(), bs.readFloat(), bs.readFloat()))# * 10000
						
						qOffs = NoeVec4((bs.readFloat(), bs.readFloat(), bs.readFloat(), bs.readFloat()))
						bs.readFloat()
						desc = T2StreamDesc(type=m_compType, offset=m_bufferOffset, stride=m_stride, bufferOffsetAddr=buffOffsAddr, sizes=sizes, qScale=qScale, qOffs=qOffs, numVerts=numVerts)
						if desc.offset < bs.getSize():
							streamDescs.append(desc)
					else:
						bs.seek(m_pStreamDesc + 24*j)
						m_numAttributes = bs.readUByte()
						m_unk  = bs.readUByte()
						m_stride  = bs.readUShort()
						m_unk2 = bs.readUByte()
						m_unk3 = bs.readUByte()
						m_unk4 = bs.readUShort()
						m_compInfoOffs = readPointerFixup()
						buffOffsAddr = bs.tell()
						m_bufferOffset = readPointerFixup()
						
						bs.seek(m_compInfoOffs)
						
						m_unkC0 = bs.readUByte()
						m_unkC1 = bs.readUByte()
						m_unkC2 = bs.readUByte()
						m_compType = bs.readUByte()
						
						streamDescs.append(StreamDesc(type=m_compType, offset=m_bufferOffset, stride=m_stride, bufferOffsetAddr=buffOffsAddr))
				
				self.submeshes.append(PakSubmesh(submeshName, m_numVertexes, m_numIndexes, m_pIndexes, streamDescs))
				self.submeshes[i].facesOffsetAddr = facesOffsetAddr
				self.submeshes[i].offset = SubmeshesOffs + self.submeshStride*i
				if dialogOptions.isTLOU2 or dialogOptions.isTLOUP1:
					self.submeshes[i].bbox = bbox
				
				self.submeshes[i].streamsAddr = m_compInfoOffs if not dialogOptions.isTLOU2 and not dialogOptions.isTLOUP1 else None
				
				if nrmRecalcDescOffs:
					bs.seek(nrmRecalcDescOffs)
					indexCount = bs.readInt()
					uknInt2 = bs.readInt()
					ptrOffsetsStart = bs.tell()
					ptr1 = readPointerFixup()
					ptr2 = readPointerFixup()
					ptr3 = readPointerFixup()
					ptr4 = readPointerFixup()
					
					self.submeshes[i].nrmRecalcDesc = [ptr1, ptr2, ptr3, ptr4, indexCount, nrmRecalcDescOffsOffset, ptrOffsetsStart]
				
				if skindataOffset:
					bs.seek(skindataOffset)
					uknSD0 = bs.readUInt()
					numWeights = bs.readUInt()
					uknSD2 = bs.readUInt()
					uknSD3 = bs.readUInt()
					bIndicesOffs = readPointerFixup(dialogOptions.isTLOUP1)
					weightsOffs = readPointerFixup(dialogOptions.isTLOUP1)
					
					self.submeshes[i].skinDesc = SkinDesc(mapOffset=bIndicesOffs, weightsOffset=weightsOffs, weightCount=numWeights, mapOffsetAddr=bs.tell()-16, weightOffsetAddr=bs.tell()-8, uncompressed=(dialogOptions.isTLOUP1 and uknSD2 > 0))
					
				bs.seek(m_material)
				shaderAssetNameOffs = readPointerFixup()
				shaderTypeOffs = readPointerFixup()
				
				
				if dialogOptions.isTLOU2 or dialogOptions.isTLOUP1:
					UUID = bs.readUInt64()
					shaderParamsOffs = readPointerFixup(True)
					texDescsListOffs = readPointerFixup()
					shaderNamesOffs = readPointerFixup()
					uknOffs = readPointerFixup()
					fetchMapDescsOffs = readPointerFixup()
					bs.seek(52*4, 1)
					paramCount = bs.readUInt()
					texCount = bs.readUInt()
					nameCount = bs.readUInt()
					fetchMapCount = bs.readUInt()
				else:
					shaderOptions0Offs = readPointerFixup()
					hashCodeOffs = readPointerFixup()
					shaderParamsOffs = readPointerFixup()
					texDescsListOffs = readPointerFixup()
					shaderOptions3Offs = readPointerFixup()
					nameCount = bs.readUInt()
					paramCount = bs.readUInt()
					texCount = bs.readUInt()
					unkCount = bs.readUInt()
				
				matName = readStringAt(bs, shaderAssetNameOffs)
				matType = readStringAt(bs, shaderTypeOffs)
				matKey = rapi.getLocalFileName(matName[:matName.find(":")])
				
				material = usedMaterials.get(m_material) 
				
				if not material:
					rapi.setPreviewOption("autoLoadNonDiffuse", "1")
					material = NoeMaterial(matKey, "")
					#materialFlags = 0
					material.setDefaultBlend(0)
					material.setSpecularColor(NoeVec4([0.5, 0.5, 0.5, 32.0])) 
					secondaryDiffuse = []
					secondaryNormal = []
					secondaryAlpha = []
					loadedDiffuse = loadedNormal = loadedTrans = loadedSpec = loadedMetal = loadedRoughness = loadedOcc = False
					
					for j in range(texCount):
						bs.seek(texDescsListOffs + (40+8*(dialogOptions.isTLOU2 or dialogOptions.isTLOUP1))*j )
						nameAddr = readPointerFixup()
						name = readStringAt(bs, nameAddr)
						bs.seek(8+8*(dialogOptions.isTLOU2 or dialogOptions.isTLOUP1),1) #path = readStringAt(bs, readPointerFixup())
						bs.seek(readPointerFixup())
						path = readStringAt(bs, readPointerFixup())
						vramHash = bs.readUInt64()
						texFileName = self.vrams[vramHash][1] if vramHash in self.vrams else ""
						doSet = False
						
						if texFileName.count("missing"):
							continue
						
						if texFileName and name.find("01") != -1:
							if not loadedDiffuse and name.find("BaseColor01") != -1:
								doSet = loadedDiffuse = vramHash 
								material.setTexture(texFileName)
							elif not loadedNormal and (name.find("Normal01") != -1 or name.find("NR") != -1):
								doSet = loadedNormal = vramHash
								material.setNormalTexture(texFileName)
								if dialogOptions.doConvertTex:
									#if gameName != "TLOUP1":
									material.flags |= noesis.NMATFLAG_NORMALMAP_FLIPY #| noesis.NMATFLAG_NORMALMAP_NODERZ
									if name.find("NR") != -1 and texFileName.find("-ao") != -1: # Ambient Occlusion
										self.vrams[vramHash][2].append(texFileName.replace(texoutExt, "_NoesisAO" + texoutExt))
										material.setOcclTexture(self.vrams[vramHash][2][len(self.vrams[vramHash][2])-1])
								
							elif not loadedTrans and name.find("Transparency01") != -1:
								doSet = loadedTrans = vramHash
								material.setOpacityTexture(texFileName)
								material.setAlphaTest(0.05)
								material.flags |= noesis.NMATFLAG_TWOSIDED
								
								if dialogOptions.doConvertTex:
									if not loadedNormal:
										self.vrams[vramHash][2].append("NoesisNRM" + texoutExt)
										material.setNormalTexture(self.vrams[vramHash][2][len(self.vrams[vramHash][2])-1])
									if not loadedDiffuse:
										self.vrams[vramHash][2].append("NoesisBrown" + texoutExt)
										material.setTexture(self.vrams[vramHash][2][len(self.vrams[vramHash][2])-1])
									
							elif not loadedSpec and name.find("pecular") != -1:
								doSet = loadedSpec = vramHash
								material.setSpecularTexture(texFileName)
								#material.flags |= noesis.NMATFLAG_PBR_SPEC
							elif not loadedOcc and name.find("Ao01") != -1:
								doSet = loadedOcc = vramHash
								material.setOcclTexture(texFileName)
								
						if not loadedDiffuse and not secondaryDiffuse and (name.find("Color0") != -1):
							secondaryDiffuse = [texFileName, vramHash]
						if not loadedNormal and not secondaryNormal and name.find("Normal0") != -1: # and not (texFileName.count("tile") or texFileName.count("detail")):
							secondaryNormal = [texFileName, vramHash]
						if not loadedTrans and not secondaryAlpha and (name.find("Transparency0") != -1):
							secondaryAlpha = [texFileName, vramHash]
								
								
						doSet = doSet or dialogOptions.loadAllTextures
						
						if doSet and texFileName and texFileName not in usedTextures:
							self.vramHashes.append(vramHash)
							usedTextures.append(texFileName)
					
					if not loadedDiffuse and secondaryDiffuse:
						loadedDiffuse = True
						material.setTexture(secondaryDiffuse[0])
						if secondaryDiffuse[0] not in usedTextures:
							self.vramHashes.append(secondaryDiffuse[1])
							usedTextures.append(secondaryDiffuse[0])
					if not loadedNormal and secondaryNormal:
						loadedNormal = True
						material.setNormalTexture(secondaryNormal[0])
						if secondaryNormal[0] not in usedTextures:
							self.vramHashes.append(secondaryNormal[1])
							usedTextures.append(secondaryNormal[0])
					if not loadedTrans and secondaryAlpha:
						loadedTrans = True
						material.setAlphaTest(0.25)
						material.setOpacityTexture(secondaryAlpha[0])
						if secondaryAlpha[0] not in usedTextures:
							self.vramHashes.append(secondaryAlpha[1])
							usedTextures.append(secondaryAlpha[0])
					
					params = {}
					setBaseColor = setSpecScale = setRoughness = setMetal = False
					outstring = "\n" + matKey + "material parameters:"
					
					for j in range(paramCount):
						bs.seek(shaderParamsOffs + 24*j)
						name = readStringAt(bs, readPointerFixup(True))
						valueOffset = readPointerFixup()
						numFloats = bs.readUInt()
						bs.seek(valueOffset)
						params[name] = [0.0, 0.0, 0.0, 1.0]
						for p in range(numFloats):
							params[name][p] = bs.readFloat()
						if numFloats == 1:
							outstring = outstring + "\n	" + name + ":  " + str(params[name][0])
						else:
							outstring = outstring + "\n	" +  name + ":  " + str(params[name])
						lowerName = name.lower()
						if True: #lowerName.find("01") != -1:
							if numFloats==3 and not setBaseColor and not material.texName and lowerName.find("basecolor") != -1:
								setBaseColor = True
								material.setDiffuseColor(params[name])
							elif numFloats==1 and not setSpecScale and not loadedMetal and not loadedRoughness and lowerName.find("spec") != -1 :
								setSpecScale = True
								material.setSpecularColor(NoeVec4([0.5*params[name][0], 0.5*params[name][0], 0.5*params[name][0], 32.0]))
							elif numFloats==1 and not setRoughness and lowerName.find("roughness") != -1:
								setRoughness = True
								material.setRoughness(params[name][0], 0.5)
							elif numFloats==1 and not setMetal and lowerName.find("metal") != -1:
								setMetal = True
								material.setMetal(params[name][0], 0.0)
					
					if dialogOptions.printMaterialParams:
						print(outstring, "\n")
						
					if dialogOptions.doConvertTex and not material.texName and ((not loadedNormal and not loadedTrans and not loadedSpec and not setBaseColor) or matKey.find("lens") != -1):
						material.setSkipRender(True)
					
					usedMaterials[m_material] = material
					self.matList.append(material)
				
				self.matNames.append(material.name)
				
				bs.seek(place)
			
			
	def loadGeometry(self, startingBonesCt=0):
		
		bs = self.bs
		rapi.rpgSetTransform((NoeVec3((GlobalScale,0,0)), NoeVec3((0,GlobalScale,0)), NoeVec3((0,0,GlobalScale)), NoeVec3((0,0,0)))) 
		
		if self.submeshes:
			
			lastLOD = 0
			
			if dialogOptions.doLoadTex:
				self.ensureTextureIndex()
				alreadyLoadedList = [tex.name for tex in self.texList]
				for vramHash in self.vramHashes:
					if vramHash not in self.vrams:
						continue
					
					tex = self.loadVRAM(self.vrams[vramHash][0])
					if tex and tex.name not in alreadyLoadedList:  
						self.texList.append(tex)
						alreadyLoadedList.append(tex.name)
					
					# Load separated channel textures and dummy textures, or merge metal+roughness into specular:
					if self.vrams[vramHash][2]: 
						for texNameOrList in self.vrams[vramHash][2]:
							if isinstance(texNameOrList, list):
								print("Found merge hash", texNameOrList[0], "for", tex.name)
								channelTex = self.loadVRAM(self.vrams[texNameOrList[0]][0])
								tex.pixelData = moveChannelsRGBA(channelTex.pixelData, texNameOrList[1], channelTex.width, channelTex.height, tex.pixelData, texNameOrList[2], tex.width, tex.height)
								
							elif texNameOrList not in alreadyLoadedList:
								dummyTex = self.loadVRAM(self.vrams[vramHash][0], texNameOrList)
								if dummyTex:
									self.texList.append(dummyTex)
									alreadyLoadedList.append(dummyTex.name)
				
				if dialogOptions.loadAllTextures:
					for vramHash, subTuple in self.vrams.items():
						if subTuple[1] and subTuple[1] not in alreadyLoadedList:
							tex = self.loadVRAM(subTuple[0])
							if tex:  
								self.texList.append(tex)
								alreadyLoadedList.append(tex.name)
			
			def movePositionsBuffer(buffer, mat, stride=12):
				if mat and stride == 12:
					posList = []
					for v in range(int(len(buffer)/12)):
						idx = 12 * v
						transVec = NoeVec3(((struct.unpack_from('f', buffer, idx))[0], (struct.unpack_from('f', buffer, idx + 4))[0], (struct.unpack_from('f', buffer, idx + 8))[0])) * mat.toQuat()
						transVec += mat[3]
						posList.extend((transVec[0], transVec[1], transVec[2]))
					buffer = struct.pack("<" + 'f'*len(posList), *posList)
				return buffer
			
			for i, sm in enumerate(self.submeshes):
				if not inc_nd_modelparts.meshMatches(sm.name, self.meshPrefixes):
					continue
				lodFind = sm.name.find("Shape")
				LODidx = int(sm.name[lodFind+5]) if lodFind != -1 and sm.name[lodFind+5].isnumeric() else 0
				if LODidx > lastLOD:
					lastLOD = LODidx
				if not dialogOptions.doLODs and LODidx > 0:
					continue
				
				rapi.rpgSetName(sm.name)
				rapi.rpgSetMaterial(self.matNames[i])
				foundUVs = foundNormals = foundColors = 0
				posBuff = []
				posStride = 12
				
				for j, sd in enumerate(sm.streamDescs):
					bs.seek(sd.offset)
					if dialogOptions.isTLOU2 or dialogOptions.isTLOUP1:
						if j == 0 and sd.stride==12:
							posBuff = bs.readBytes(sd.stride * sm.numVerts)
							posStride = sd.stride
						elif sd.type == 1:
							rapi.rpgBindUV1Buffer(bs.readBytes(4 * sm.numVerts), noesis.RPGEODATA_HALFFLOAT, 4)
						elif sd.type == 2:
							rapi.rpgBindNormalBuffer(bs.readBytes(4 * sm.numVerts), noesis.RPGEODATA_BYTE, 4)
						elif sd.type == 3:
							rapi.rpgBindTangentBuffer(bs.readBytes(4 * sm.numVerts), noesis.RPGEODATA_BYTE, 4)
						elif sd.type == 11:
							rapi.rpgBindUV2Buffer(bs.readBytes(4 * sm.numVerts), noesis.RPGEODATA_HALFFLOAT, 4)
						else:
							floatsList = []
							for v in range(sd.numVerts):
								for c in range(4):
									if sd.sizes[c]:
										floatsList.append(bs.readBits(sd.sizes[c]) * sd.qScale[c] + sd.qOffs[c])
									elif sd.type == 64 and c < 3:
										floatsList.append(sd.qScale[c] + sd.qOffs[c])
							floatsBuffer = struct.pack("<" + 'f'*len(floatsList), *floatsList)
							#try:
							if floatsBuffer:
								if sd.type == 64:
									posBuff = floatsBuffer
								elif sd.type == 65: 
									rapi.rpgBindUV1Buffer(floatsBuffer, noesis.RPGEODATA_FLOAT, 8)
								elif sd.type == 75: 
									rapi.rpgBindUV2Buffer(floatsBuffer, noesis.RPGEODATA_FLOAT, 8)
								elif sd.type == 76: 
									rapi.rpgBindUVXBuffer(floatsBuffer, noesis.RPGEODATA_FLOAT, 8, 2, sd.numVerts)
								else:
									print("Buffer type not read:", sd.type)
							#except:
							#	print("Failed to bind buffer type", sd.type)
					else:
						#Positions
						if j == 0:
							posBuff = bs.readBytes(sd.stride * sm.numVerts)
							posStride = sd.stride
						#UVs
						elif sd.type == 34: 
							foundUVs += 1
							rapi.rpgBindUVXBuffer(bs.readBytes(4 * sm.numVerts), noesis.RPGEODATA_HALFFLOAT, 4, foundUVs-1, sm.numVerts)
						#Normals/Tangents
						elif sd.type == 31 and foundNormals != 2:
							foundNormals += 1
							if foundNormals == 1:
								rapi.rpgBindNormalBuffer(bs.readBytes(4 * sm.numVerts), noesis.RPGEODATA_BYTE, 4)
							elif foundNormals == 2:
								rapi.rpgBindTangentBuffer(bs.readBytes(4 * sm.numVerts), noesis.RPGEODATA_BYTE, 4)
						#Extra vec4 halfs
						elif sd.type == 10:
							foundColors += 1
							if dialogOptions.readColors  and foundColors == 1:
								rapi.rpgBindColorBufferOfs(bs.readBytes(8 * sm.numVerts), noesis.RPGEODATA_HALFFLOAT, 4, 0, 4)
							else:
								self.userStreams[i] = self.userStreams.get(i) or []
								self.userStreams[i].append(NoeUserStream("Vec4Halfs_" + str(foundColors-1), bs.readBytes(8 * sm.numVerts), 8, 0))
						else:
							print("Omitting vertex component type", sd.type, "found at", bs.tell())
				
				if self.boneList and sm.skinDesc:
					bs.seek(sm.skinDesc.mapOffset)
					vertWeightOffsets = []
					for b in range(sm.numVerts):
						vertWeightOffsets.append((bs.readUInt(), bs.readUInt()))
					idsList = []
					weightList = []
					for v, offsetsCounts in enumerate(vertWeightOffsets):
						bs.seek(sm.skinDesc.weightsOffset + offsetsCounts[1])
						for w in range(12):
							if w >= offsetsCounts[0]:
								weightList.append(0)
								idsList.append(0)
							elif sm.skinDesc.uncompressed:
								weightList.append(bs.readFloat())
								idsList.append(bs.readUInt() + startingBonesCt)
							else:
								weightList.append(bs.readBits(22))
								idsList.append(bs.readBits(10) + startingBonesCt)
					if noesis.optWasInvoked('-anims'):
						activeBones = set(bone for bone, weight in zip(idsList, weightList) if weight > 0)
						print('[NDAnim] mesh %s: %d active skin bones' % (sm.name, len(activeBones)))
					if SkinSamples is not None and posStride == 12 and not self.xforms.get(sm.offset) \
							and not re.search(SkinSampleExcludedMeshes, sm.name.lower()):
						weightScale = 1.0 if sm.skinDesc.uncompressed else 1.0 / 0x3FFFFF
						for v in range(sm.numVerts):
							influences = [(idsList[v * 12 + w], weightList[v * 12 + w] * weightScale)
								for w in range(12) if weightList[v * 12 + w] > 0]
							SkinSamples.append((struct.unpack_from('<3f', posBuff, v * 12), influences))
					if sm.skinDesc.uncompressed:
						rapi.rpgBindBoneIndexBufferOfs(struct.pack("<" + 'I'*len(idsList), *idsList), noesis.RPGEODATA_UINT, 48, 0, 12)
						rapi.rpgBindBoneWeightBufferOfs(struct.pack("<" + 'f'*len(weightList), *weightList), noesis.RPGEODATA_FLOAT, 48, 0, 12)
					else:
						rapi.rpgBindBoneIndexBufferOfs(struct.pack("<" + 'H'*len(idsList), *idsList), noesis.RPGEODATA_USHORT, 24, 0, 12)
						rapi.rpgBindBoneWeightBufferOfs(struct.pack("<" + 'I'*len(weightList), *weightList), noesis.RPGEODATA_UINT, 48, 0, 12)
						
				bs.seek(sm.facesOffset)
				faceBuffer = bs.readBytes(2 * sm.numIndices)
				instanceList = self.xforms.get(sm.offset) or [None]
				firstPosBuff = movePositionsBuffer(posBuff, instanceList[0], posStride)
				success = False
				
				try:
					idxList = [(struct.unpack_from('H', faceBuffer, i*2))[0] for i in range(int(len(faceBuffer)/2))]
					rapi.rpgBindPositionBuffer(firstPosBuff, noesis.RPGEODATA_FLOAT if posStride==12 else noesis.RPGEODATA_HALFFLOAT, posStride)
					rapi.rpgCommitTriangles(faceBuffer, noesis.RPGEODATA_USHORT, sm.numIndices, noesis.RPGEO_TRIANGLE, 0x1)
					success = True
				except:
					print("Failed to bind submesh", i)
					
				if success:
					for i in range(1, len(instanceList)):
						rapi.rpgSetName(sm.name + "#" + str(i))
						rapi.rpgBindPositionBuffer(movePositionsBuffer(posBuff, instanceList[i], posStride), noesis.RPGEODATA_FLOAT if posStride==12 else noesis.RPGEODATA_HALFFLOAT, posStride)
						rapi.rpgCommitTriangles(faceBuffer, noesis.RPGEODATA_USHORT, sm.numIndices, noesis.RPGEO_TRIANGLE, 0x1)
				
				rapi.rpgClearBufferBinds()
				
			print("\n====================================\n\"" + rapi.getLocalFileName(self.path or rapi.getInputName()) + "\" Textures list:")
			sortedTupleList = sorted([ (subTuple[1], subTuple[0]) for hash, subTuple in self.vrams.items() ])
			for sortTuple in sortedTupleList:
				if sortTuple[0]:
					print("    " + sortTuple[0].replace(".tga", texoutExt) + "  --  " + str(dxFormat.get(readUIntAt(bs, sortTuple[1]+72))))
			print("")
			
		else:
			print("Geometry data not found!")
			
		return 1

def _presetModelPartSpecs(mainPakPath, existingSpecs):
	"""Apply named GUI part presets without duplicating explicitly selected parts."""
	presetPath = os.path.join(noesis.getPluginsPath(), "python", "NDPartPresets.json")
	try:
		with open(presetPath, 'r', encoding='utf-8') as source:
			presets = json.load(source)
	except (IOError, OSError):
		return []
	except ValueError as e:
		print('[NDParts] preset file is not valid JSON, ignored:', presetPath, e)
		return []
	if not isinstance(presets, dict) or presets.get('version') != 1 \
			or not isinstance(presets.get('presets'), dict):
		print('[NDParts] preset file has an unsupported layout, ignored:', presetPath)
		return []
	mainName = os.path.basename(mainPakPath).lower()
	entry = None
	for key, value in presets['presets'].items():
		if key.lower() == mainName:
			entry = value
			break
	if entry is None:
		return []
	try:
		specs = inc_nd_modelparts.readModelPartSpecs(entry)
	except ValueError as e:
		print('[NDParts] preset for %s is invalid, ignored: %s' % (mainName, e))
		return []
	mainDir = os.path.dirname(mainPakPath)
	def resolved(path):
		return os.path.normcase(inc_nd_config.resolve_asset_path(path, BaseDirectories, mainDir)).lower()
	taken = set([resolved(mainPakPath)])
	for spec in existingSpecs:
		taken.add(resolved(spec['file']))
	fresh = [spec for spec in specs if resolved(spec['file']) not in taken]
	if fresh:
		print('[NDParts] preset for %s: adding %d part(s) from NDPartPresets.json'
			% (mainName, len(fresh)))
	return fresh

def pakLoadModel(data, mdlList):

	global dialogOptions, gameName, SkinSamples
	SkinSamples = [] if (LoadAnimations or noesis.optWasInvoked('-anims')) and not noesis.optWasInvoked('-ndlegacyhelpers') else None
	dialogOptions.textureIndexAttempts = set()
	if noesis.optWasInvoked("-ndtextures"):
		dialogOptions.doLoadTex = True
	
	noesis.logPopup()
	print("\n\n	Naughty Dog PAK model import", Version, "by alphaZomega\n")
	
	if noesis.optWasInvoked("-lods"):
		dialogOptions.doLODs = True
	
	#Close existing dialog (if open)
	if dialogOptions.dialog and dialogOptions.dialog.isOpen:
		dialogOptions.dialog.isOpen = False
		dialogOptions.dialog.isCancelled = True
		dialogOptions.dialog.noeWnd.closeWindow()
	
	noDialog = noesis.optWasInvoked("-nodialog") or NoDialog
	pak = PakFile(NoeBitStream(data), {'path':rapi.getInputName()})
	ctx = rapi.rpgCreateContext()
	gameName = getGameName()
	dialog = None
	if noesis.optWasInvoked("-ndrebuildtextures"):
		pak.dumpGlobalVramHashes()
		dialogOptions.texDicts = None

	if not noDialog:
		pak.readPakHeader()
		dialog = openOptionsDialogWindow(None, None, {"pak":pak})
		dialog.createPakWindow()
		pak.readPak()
	else:
		pak.readPak()
	
	if not noDialog and dialog.isCancelled:
		mdlList.append(NoeModel())
	else:
		# Animation scan runs BEFORE geometry: animation paks contain no
		# meshes, so loadGeometry()/rpgConstructModelAndSort() can fail and
		# would otherwise skip this entirely.
		animClips = []
		loadAnims = LoadAnimations or noesis.optWasInvoked("-anims")
		if loadAnims and HaveAnimSupport:
			try:
				print('[NDAnim] decoder version:', inc_nd_anim.ANIM_DECODER_VERSION)
				animClips = list(_readPakAnimations(pak))
				if noesis.optWasInvoked('-animfile'):
					animationPath = noesis.optGetArg('-animfile')
					animationPak = PakFile(NoeBitStream(rapi.loadIntoByteArray(animationPath)), {'path': animationPath})
					animClips.extend(_readPakAnimations(animationPak))
				if noesis.optWasInvoked('-animclip'):
					wanted = noesis.optGetArg('-animclip').lower()
					animClips = [clip for clip in animClips if clip.name.lower() == wanted]
				print('[NDAnim] found %d requested animation resource(s)' % len(animClips))
			except Exception as e:
				print("[NDAnim] animation scan failed:", e)
				if noesis.optWasInvoked('-animfile'):
					return 0

		if noDialog:
			needSkelForAnims = bool(animClips) and not pak.boneList and not pak.submeshes
			if ((pak.submeshes and pak.submeshes[0].skinDesc and not pak.boneList) or needSkelForAnims) and dialogOptions.doLoadBase:
				guessedName = None
				if needSkelForAnims:
					# exact match by the clips' SkelID beats filename guessing
					guessedName = _findSkelPakForAnims(animClips, gameName)
					if guessedName:
						print("Animation SkelID matched skeleton:", guessedName)
				if not guessedName:
					guessedName = pak.path.replace(".pak", ".skel.pak")
					bestHint = ''
					for key, value in baseSkeletons[gameName].items():
						if key in pak.path.lower() and len(key) > len(bestHint):
							guessedName = BaseDirectories[gameName] + value
							bestHint = key
				skelPath = guessedName

				if not skelPath or not rapi.checkFileExists(skelPath):
					print('[NDAnim] required skeleton was not found:', skelPath)
					return 0
				if skelPath and rapi.checkFileExists(skelPath):
					pak.basePak = PakFile(NoeBitStream(rapi.loadIntoByteArray(skelPath)), {'path':skelPath})
					pak.basePak.readPak()
					pak.boneList = pak.basePak.boneList
					pak.boneMap = pak.basePak.boneMap
					pak.boneDict = pak.basePak.boneDict
				else:
					print("Failed to load Skeleton", skelPath or "[No path found]")
			pak.loadGeometry()
			extraParts = inc_nd_modelparts.readModelPartSpecs(noesis.optGetArg('-modelparts')) if noesis.optWasInvoked('-modelparts') else []
		else:
			pak.loadGeometry()
			extraParts = inc_nd_modelparts.readModelPartSpecs(dialog.fullLoadItems)
			if noesis.optWasInvoked('-modelparts'):
				extraParts.extend(inc_nd_modelparts.readModelPartSpecs(noesis.optGetArg('-modelparts')))
			else:
				extraParts.extend(_presetModelPartSpecs(pak.path, extraParts))
		for partSpec in extraParts:
			fullOtherPath = inc_nd_config.resolve_asset_path(
				partSpec['file'], BaseDirectories, os.path.dirname(pak.path))
			if not fullOtherPath or os.path.normcase(os.path.normpath(fullOtherPath)) == os.path.normcase(os.path.normpath(pak.path)):
				continue
			if not rapi.checkFileExists(fullOtherPath):
				print('[NDAnim] additional model part not found:', fullOtherPath)
				return 0
			otherPak = PakFile(NoeBitStream(rapi.loadIntoByteArray(fullOtherPath)),
				{'path': fullOtherPath, 'meshPrefixes': partSpec['meshPrefixes']})
			otherPak.texList = pak.texList
			otherPak.matList = pak.matList
			otherPak.doLODs = pak.doLODs
			if partSpec.get('attachTo'):
				otherPak.readPakHeader()
				if not otherPak.jointOffset:
					raise ValueError('attached part has no skeleton: ' + fullOtherPath)
				otherPak.readPak()
				startingBonesCt, matched = inc_nd_modelparts.attachPartBones(
					pak.boneList, otherPak.boneList, partSpec.get('boneSuffix'),
					partSpec['attachTo'], rapi.getExtensionlessName(rapi.getLocalFileName(fullOtherPath)))
				print('[NDParts] %s: attached %d bones, %d driver anchors, fallback=%s' %
					(rapi.getLocalFileName(fullOtherPath), len(otherPak.boneList), matched, partSpec['attachTo']))
			else:
				otherPak.boneList = pak.boneList
				startingBonesCt = len(pak.boneList) if pak.boneList else 0
				otherPak.readPak()
				if otherPak.jointOffset is None:
					startingBonesCt = 0
			selected = [mesh for mesh in otherPak.submeshes
				if inc_nd_modelparts.meshMatches(mesh.name, otherPak.meshPrefixes)]
			if otherPak.meshPrefixes and not selected:
				raise ValueError('model-part mesh filter matched nothing: ' + fullOtherPath)
			print('[NDParts] %s: selected %d mesh descriptors' %
				(rapi.getLocalFileName(fullOtherPath), len(selected)))
			if loadAnims and HaveAnimSupport:
				animClips.extend(_readPakAnimations(otherPak))
			otherPak.loadGeometry(startingBonesCt)
		try:
			mdl = rapi.rpgConstructModelAndSort()
		except:
			print ("Failed to construct model")
			mdl = NoeModel()
			
		if pak.texList:
			mdl.setModelMaterials(NoeModelMaterials(pak.texList, pak.matList))
		
		mdlList.append(mdl)
		
		if pak.boneList:
			pak.boneList = rapi.multiplyBones(pak.boneList)
			if dialog and len(dialog.loadItems) > 1:
				for bone in pak.boneList:
					if bone.name.find("root_hair") != -1:
						bone.parentName = "headb" 
						break
			for mdl in mdlList:
				mdl.setBones(pak.boneList)
				
		if pak.userStreams:
			for meshIdx, userStreamList in pak.userStreams.items():
				if userStreamList and meshIdx < len(mdl.meshes):
					mdl.meshes[meshIdx].setUserStreams(userStreamList)

		loadAnims = LoadAnimations or noesis.optWasInvoked("-anims")
		if loadAnims and HaveAnimSupport and animClips:
			try:
				inc_nd_anim.resolveChannelNames(animClips, pak.boneList)
				animBoneMap = getattr(pak, "boneMap", None)
				if not animBoneMap and getattr(pak, "basePak", None):
					animBoneMap = getattr(pak.basePak, "boneMap", None)
				targetSkelId = _pakSkeletonId(pak)
				if targetSkelId is None:
					print('[NDAnim] cannot verify the target skeleton; animation attachment refused')
					return 1
				animBindPose = inc_nd_anim.bindPoseFromJointHierarchy(
					pak, inc_nd_anim.NDAnimReader(pak), ResItemPaddingSz)
				clipName = noesis.optGetArg('-animclip') if noesis.optWasInvoked('-animclip') else None
				print('[NDAnim] target skeleton ID: %08X; cross-skeleton retargeting disabled' % targetSkelId)
				deformationRig = pak.deformationRig
				if deformationRig is None and pak.basePak:
					deformationRig = pak.basePak.deformationRig
				if deformationRig and SkinSamples:
					anchored = deformationRig.useSkinStrapAnchors(SkinSamples)
					if anchored:
						print('[NDRig] strap anchors taken from body skin weights: %d of %d strap bases' % (anchored,
							sum(1 for constraint in deformationRig.constraints.values() if constraint['kind'] == 'strap')))
				SkinSamples = None
				if deformationRig and deformationRig.constraints:
					print('[NDRig] baking bind-preserving twist/strap compatibility constraints; native RBF and cloth physics are not evaluated')
				anims = inc_nd_anim.buildNoesisAnims(
					animClips, pak.boneList, boneMap=animBoneMap or None,
					scale=GlobalScale, bindPose=animBindPose,
					targetSkelId=targetSkelId, clipName=clipName,
					allowAdditive=noesis.optWasInvoked('-animadditive'),
					deformationRig=deformationRig)
				if anims:
					for m in mdlList:
						m.setAnims(anims)
					print("[NDAnim] attached %d animation(s)" % len(anims))
				else:
					print("[NDAnim] %d clip(s) found, but none produced keyframes:"
						  " they carry no per-frame joint data, or the bone"
						  " indices fall outside this skeleton."
						  % len(animClips))
			except Exception as e:
				print("[NDAnim] animation attach failed:", e)
		
	return 1

def pakWriteModel(mdl, bs):
	
	global pointerPageIds, pakPageEntries, gameName
	
	noesis.logPopup()
	print("\n\n	Naughty Dog PAK model export", Version, "by alphaZomega\n")
	gameName = getGameName()
	
	def getExportName(fileName):		
		if fileName == None:
			injectMeshName = re.sub(r'out\w+\.', '.', rapi.getOutputName().lower()).replace("fbx",".").replace("out.pak",".pak")
			splittedTarget = injectMeshName.split("ncharted4_data", 1)
			splittedSource = BaseDirectories[gameName].split("ncharted4_data", 1)
			if len(splittedTarget) > 1 and len(splittedSource) > 1 and rapi.checkFileExists(splittedSource[0] + "ncharted4_data" + splittedTarget[1].replace(".orig", "")): 
				injectMeshName = splittedSource[0] + "ncharted4_data" + splittedTarget[1].replace(".orig", "")
			if rapi.checkFileExists(injectMeshName.replace(".pak", ".orig.pak")):
				injectMeshName = injectMeshName.replace(".pak", ".orig.pak")
		else:
			injectMeshName = fileName
		injectMeshName = noesis.userPrompt(noesis.NOEUSERVAL_FILEPATH, "Export .pak", "Choose a .pak file to inject", injectMeshName, None)
		
		if injectMeshName == None:
			print("Aborting...")
			return
		return injectMeshName
		
	fileName = None
	if noesis.optWasInvoked("-meshfile"):
		injectMeshName = noesis.optGetArg("-meshfile")
	else:
		injectMeshName = getExportName(fileName)
	
	if injectMeshName == None:
		return 0
	while not (rapi.checkFileExists(injectMeshName)):
		print ("File not found!")
		if noesis.optGetArg("-meshfile"):
			print(injectMeshName, "\n", getExportName(fileName))
		injectMeshName = getExportName(fileName)	
		fileName = injectMeshName
		if injectMeshName == None or noesis.optGetArg("-meshfile"):
			return 0
			
	srcMesh = rapi.loadIntoByteArray(injectMeshName)
	
	if noesis.optWasInvoked("-lods"):
		dialogOptions.doLODs = True
	
	f = NoeBitStream(srcMesh)
	magic = readUIntAt(f, 0) 
	if magic != 2681 and magic != 68217 and magic != 2147486329 and magic != 2685:
		print("Not a known .pak file.\nAborting...")
		return 0
	
	#copy file:
	bs.writeBytes(f.readBytes(f.getSize()))
	
	source = PakFile(f)
	for hint, fileName in baseSkeletons[gameName].items():
		if rapi.getOutputName().find(hint) != -1:
			dialogOptions.baseSkeleton = fileName
			
	source.readPakHeader()
	if source.geometryVersion == 57:
		print('Geometry version 57 supports model import/FBX export only; PAK rewriting is not validated.')
		return 0
	texOnly = noesis.optWasInvoked("-t") or dialogOptions.isTLOU2 or dialogOptions.isTLOUP1
	if texOnly:
		print("Embedding textures only")
	
	if source.needsBasePak and not rapi.checkFileExists(BaseDirectories[gameName] + (dialogOptions.baseSkeleton or "")):
		dialogOptions.baseSkeleton = injectMeshName.replace(".pak", "-base.pak")
		while dialogOptions.baseSkeleton != None and not rapi.checkFileExists(dialogOptions.baseSkeleton):
			dialogOptions.baseSkeleton = noesis.userPrompt(noesis.NOEUSERVAL_FILEPATH, "Skeleton Not Found", "Input the path to the .pak containing this model's skeleton", dialogOptions.baseSkeleton, None) 
		if not dialogOptions.baseSkeleton:
			print("No base skeleton was found for skinned mesh, aborting...")
			return 0
			
	source.readPak()
	
	boneDict = {}
	if not texOnly or (noesis.optWasInvoked("-bones") and source.basePak.jointOffset):
		try:
			for i, bone in enumerate(source.boneList):
				boneDict[bone.name] = i
		except:
			print("ERROR: Could not load base skeleton")
	
	if source.submeshes:
		
		doWrite = didAppend = False
		lastLOD = 0
		isNoesisSplit = (mdl.meshes[0].name[:5] == "0000_")
		fbxMeshList = mdl.meshes if not isNoesisSplit else recombineNoesisMeshes(mdl)
		
		f.seek(source.geoOffset[0] + source.geoOffset[1] + 72)
		submeshesAddr = source.readPointerFixup()
		
		newPak = PakFile(bs)
		newPak.readPak()
		
		#write new bone positions:
		newBaseFile = ""
		if noesis.optWasInvoked("-bones") and (source.basePak or newPak).jointOffset:
			bonePak = source.basePak or newPak
			bonestream = bonePak.bs
			ji = bonePak.jointsInfo
			for i, bone in enumerate(mdl.bones):
				boneID = boneDict.get(bone.name)
				if boneID != None and boneID in bonePak.boneMap:
					xformBoneID = bonePak.boneMap.index(boneID)
					boneMat = bone.getMatrix()
					if bone.parentIndex != -1:
						boneMat = boneMat * mdl.bones[bone.parentIndex].getMatrix().inverse()
					boneMat = boneMat.transpose()
					bonestream.seek(ji.transformsStart + xformBoneID*48 + 16)
					bonestream.writeBytes(boneMat.toQuat().toBytes())
					bonestream.writeBytes((boneMat[3] * (1/GlobalScale)).toBytes())
					bonestream.seek(ji.parentingStart + boneID*16 + 4)
					if bone.parentIndex == -1:
						bonestream.writeInt(-1)
					elif boneDict.get(bone.parentName):
						bonestream.writeInt(boneDict.get(bone.parentName))
			if source.basePak:
				newBaseFile = rapi.getOutputName().replace(rapi.getLocalFileName(rapi.getOutputName()), rapi.getLocalFileName(bonePak.path)).replace(".NEW", "").replace(".pak", ".NEW.pak")
				open(newBaseFile, "wb").write(bonestream.getBuffer())
		
		if not texOnly:
			
			meshesToInject = []
			submeshesFound = []
			
			for i, sm in enumerate(source.submeshes):
				writeMesh = None
				for mesh in fbxMeshList:
					fixMeshName = rapi.getExtensionlessName(mesh.name)
					if fixMeshName == sm.name:
						writeMesh = mesh
						submeshesFound.append(fixMeshName)
						break
				if not writeMesh:
					blankMeshName = sm.name
					blankTangent = NoeMat43((NoeVec3((0,0,0)), NoeVec3((0,0,0)), NoeVec3((0,0,0)), NoeVec3((0,0,0)))) 
					blankWeight = NoeVertWeight([0], [1])
					writeMesh = NoeMesh([0, 1, 2], [NoeVec3((0.00000000001,0,0)), NoeVec3((0,0.00000000001,0)), NoeVec3((0,0,0.00000000001))], blankMeshName, blankMeshName, -1, -1) #positions and faces
					writeMesh.setUVs([NoeVec3((0,0,0)), NoeVec3((0,0,0)), NoeVec3((0,0,0))]) #UV1
					writeMesh.setUVs([NoeVec3((0,0,0)), NoeVec3((0,0,0)), NoeVec3((0,0,0))], 1) #UV2
					writeMesh.setTangents([blankTangent, blankTangent, blankTangent]) #Normals + Tangents
					writeMesh.setWeights([blankWeight,blankWeight,blankWeight]) #Weights + Indices
				meshesToInject.append((writeMesh, sm))
			
			if len(submeshesFound) > 0:
				doWrite = True
				print("Found the following submeshes to inject from FBX:")
				for meshName in submeshesFound: 
					print("    " + meshName)
				print("\nAll other submeshes in this pak will be turned into invisible placeholders.\n")
			else:
				print("Found no submeshes to inject from FBX, copying original file...")
			
			if doWrite:
				
				wb = NoeBitStream()
				newPageStreams = []
				pageCt = readUIntAt(f, 16)
				pointerFixupPageCt = readUIntAt(bs, 24)
				pointerFixupTblOffs = readUIntAt(bs, 28)
				isModded = (readUIntAt(f, pointerFixupTblOffs + 12*8) == 4294967295)
				newPage = pageCt if not isModded else pageCt-1
				
				for i, meshTuple in enumerate(meshesToInject):
					
					writeMesh = meshTuple[0]
					sm = meshTuple[1]
					lodFind = sm.name.find("Shape")
					LODidx = int(sm.name[lodFind+5]) if lodFind != -1 and sm.name[lodFind+5].isnumeric() else 0
					if LODidx > lastLOD:
						lastLOD = LODidx
					if not dialogOptions.doLODs and LODidx > 0:
						if len(writeMesh.positions) == 3:# and LODidx  and lodIdx < :
							continue
					
					print("Injecting ", writeMesh.name)
					appendedPositions = appendedWeights = appendedIndices = isModded #False
					newPageDataAddr = source.pakPageEntries[len(source.pakPageEntries)-1][0] + source.pakPageEntries[len(source.pakPageEntries)-1][1]
					owningIndex = source.pakPageEntries[len(source.pakPageEntries)-1][2]
					vertOffs = submeshesAddr + 176*i + 36
					foundPositions = foundUVs = foundNormals = 0
					appendedPositions = (len(writeMesh.positions) > sm.numVerts) or appendedPositions #and (not isModded or (source.getPointerFixupPage(sm.streamDescs[0].bufferOffsetAddr) < pageCt-1))
					tempbs = wb if appendedPositions else bs
					wroteColors = False
					
					bs.seek(sm.streamsAddr)
					#sdBytesList = []
					#finalSdBytesList = []
					#for j, sd in enumerate(sm.streamDescs):
					#	sdBytesList.append(bs.readBytes(24))
					
					for j, sd in enumerate(sm.streamDescs):
						bs.seek(sd.offset)
						
						
						if appendedPositions and wb.tell() + sd.stride * len(writeMesh.positions) > 1048032: #pages have a maximum size
							newPageStreams.append(wb)
							newPage += 1
							wb = NoeBitStream()
							tempbs = wb
							
						if appendedPositions: 
							newPak.changePointerFixup(sd.bufferOffsetAddr, wb.tell(), newPage)
						
						bufferStart = tempbs.tell()
						for b in range(sd.stride * len(writeMesh.positions)):
							tempbs.writeByte(0)
						bufferEnd = tempbs.tell()
						tempbs.seek(bufferStart)
						
						if ((j == 0 and sd.stride == 12 or sd.stride == 8)) and not foundPositions:
							bFoundPositions = True
							#finalSdBytesList.append(sdBytesList[j])
							if sd.stride == 12:
								for v, vert in enumerate(writeMesh.positions):
									tempbs.writeFloat(vert[0] * (1/GlobalScale))
									tempbs.writeFloat(vert[1] * (1/GlobalScale))
									tempbs.writeFloat(vert[2] * (1/GlobalScale))
							elif sd.stride == 8:
								for v, vert in enumerate(writeMesh.positions):
									tempbs.writeHalfFloat(vert[0] * (1/GlobalScale))
									tempbs.writeHalfFloat(vert[1] * (1/GlobalScale))
									tempbs.writeHalfFloat(vert[2] * (1/GlobalScale))
									tempbs.writeHalfFloat(0)
									
						elif sd.type == 34:
							foundUVs += 1
							UVs = []
							if foundUVs == 1 and writeMesh.uvs:
								UVs = writeMesh.uvs
							elif foundUVs == 2 and writeMesh.lmUVs:
								UVs = writeMesh.lmUVs
							elif foundUVs > 2 and writeMesh.uvxList and len(writeMesh.uvxList) > foundUVs-3:
								UVs = writeMesh.uvxList[foundUVs-3]
							
							if len(UVs) == len(writeMesh.positions):
								for v, vert in enumerate(UVs):
									tempbs.writeHalfFloat(vert[0])
									tempbs.writeHalfFloat(vert[1])
							else:
								for v, vert in enumerate(writeMesh.positions):
									tempbs.writeHalfFloat(0)
									tempbs.writeHalfFloat(0)
									
						elif sd.type == 31 and foundNormals < 2:
							foundNormals += 1
							#finalSdBytesList.append(sdBytesList[j])
							if foundNormals == 1:
								for v, vert in enumerate(writeMesh.tangents): 
									tempbs.writeByte(int(vert[0][0] * 127 + 0.5000000001)) #normal
									tempbs.writeByte(int(vert[0][1] * 127 + 0.5000000001))
									tempbs.writeByte(int(vert[0][2] * 127 + 0.5000000001))
									tempbs.writeByte(0)
							elif foundNormals == 2: 
								for v, vert in enumerate(writeMesh.tangents):
									tempbs.writeByte(int(vert[2][0] * 127 + 0.5000000001)) #bitangent
									tempbs.writeByte(int(vert[2][1] * 127 + 0.5000000001))
									tempbs.writeByte(int(vert[2][2] * 127 + 0.5000000001))
									TNW = vert[0].cross(vert[1]).dot(vert[2])
									if (TNW < 0.0):
										tempbs.writeByte(129)
									else:
										tempbs.writeByte(127)
						
						elif sd.type == 10:
							if writeMesh.colors and not wroteColors:
								wroteColors = True
								#finalSdBytesList.append(sdBytesList[j])
								for v, vert in enumerate(writeMesh.colors):
									tempbs.writeHalfFloat(vert[0])
									tempbs.writeHalfFloat(vert[1])
									tempbs.writeHalfFloat(vert[2])
									tempbs.writeHalfFloat(0)
							else:
								for v in range(len(writeMesh.positions)):
									tempbs.writeHalfFloat(0)
									tempbs.writeHalfFloat(0)
									tempbs.writeHalfFloat(0)
									tempbs.writeHalfFloat(0)
									
						else:
							print("Nulling unknown component type", sd.type)

						
						#bs.seek(sm.streamsAddr)
						#for rawBytes in finalSdBytesList:
						#	bs.writeBytes(rawBytes)
						
						tempbs.seek(bufferEnd)
						if bufferEnd - bufferStart > 0:
							writeUIntAt(bs, sd.bufferOffsetAddr - 12, bufferEnd-bufferStart) #buffer size
					
					
					if sm.skinDesc:
					
						fbxWeightCount = 0
						trueWeightCounts = []
						for v, vertWeight in enumerate(writeMesh.weights):
							trueWeightCounts.append(0)
							for w, weight in enumerate(vertWeight.weights):
								if weight > 0: 
									fbxWeightCount += 1
									trueWeightCounts[v] += 1
									
						if appendedPositions and wb.tell() > 0 and wb.tell() + 8*len(writeMesh.positions) > 1048032:
							newPageStreams.append(wb)
							newPage += 1
							wb = NoeBitStream()
							
						runningOffset = boneID = 0
						idxStart = wb.tell() if appendedPositions else sm.skinDesc.mapOffset
						idxbs = wb if appendedPositions else bs
						
						if appendedPositions:
							newPak.changePointerFixup(sm.skinDesc.mapOffsetAddr, idxStart, newPage)
							for vertWeight in writeMesh.weights:
								wb.writeUInt64(0)
						
						appendedWeights = (fbxWeightCount > sm.skinDesc.weightCount)
						if appendedWeights:
							if 4*fbxWeightCount > 1048032:
								print("\nWARNING: Weights buffer exceeds the maximum page size of 262008 weights (has " + str(fbxWeightCount) + ")!\n")
							if wb.tell() > 0 and wb.tell() + 4*fbxWeightCount > 1048032:
								newPageStreams.append(wb)
								newPage += 1
								wb = NoeBitStream()
							newPak.changePointerFixup(sm.skinDesc.weightOffsetAddr, wb.tell(), newPage)
							
						wtStart = wb.tell() if appendedWeights else sm.skinDesc.weightsOffset
						tempbs = wb if appendedWeights else bs
						
						for v, vertWeight in enumerate(writeMesh.weights):
							idxbs.seek(idxStart + 8*v)
							idxbs.writeUInt(trueWeightCounts[v])
							idxbs.writeUInt(runningOffset)
							tempbs.seek(wtStart + runningOffset)
							for w, weight in enumerate(vertWeight.weights):
								if weight > 0:
									try:
										boneID = boneDict[mdl.bones[vertWeight.indices[w]].name]
									except:
										print("Bone weight ID", w, mdl.bones[vertWeight.indices[w]].name, "not found in pak skeleton")
									tempbs.writeUInt((boneID << 22) | int(weight * 4194303))
									runningOffset += 4
						
						writeUIntAt(bs, sm.skinDesc.mapOffsetAddr-12, int(runningOffset/4))
					
					if len(writeMesh.indices) > sm.numIndices:
						appendedIndices = True
						if appendedIndices and wb.tell() + 6*len(writeMesh.positions) > 1048032:
							newPageStreams.append(wb)
							newPage += 1
							wb = NoeBitStream()
							
						newPak.changePointerFixup(sm.facesOffsetAddr, wb.tell(), newPage)
						for k, idx in enumerate(writeMesh.indices):
							wb.writeUShort(idx)
					else:
						bs.seek(sm.facesOffset)
						for k, idx in enumerate(writeMesh.indices):
							bs.writeUShort(idx)
					
					while (wb.tell() % 16 != 0): 
						wb.writeByte(0)
					wb.writeUInt64(0)
					wb.writeUInt(0)
						
					#Null out normals recalculation values:
					if sm.nrmRecalcDesc:
						#bs.seek(sm.nrmRecalcDesc[5])
						#bs.writeUInt64(0)
						
						nrmStart = wb.tell()
						for n in range(4):
							bs.seek(sm.nrmRecalcDesc[n])
							appendedPositions = appendedPositions or (len(writeMesh.positions) > readUIntAt(bs, sm.nrmRecalcDesc[6]-8))
							tempbs = wb if appendedPositions else bs
							if appendedPositions: 
								if n == 0 and wb.tell() + 2 * len(writeMesh.positions) > 1048032:
									newPageStreams.append(wb)
									newPage += 1
									wb = NoeBitStream()
									tempbs = wb
								newPak.changePointerFixup(sm.nrmRecalcDesc[6] + 8*n, nrmStart, newPage)
							if not appendedPositions or n == 0:
								for k in range(len(writeMesh.positions)):
									tempbs.writeShort(0)
							writeUIntAt(bs, sm.nrmRecalcDesc[6]-8, len(writeMesh.positions))
							writeUIntAt(bs, sm.nrmRecalcDesc[6]-4, len(writeMesh.indices))
					
					#set vertex/index counts:
					bs.seek(vertOffs)
					bs.writeUInt(len(writeMesh.positions))
					bs.writeUInt(len(writeMesh.indices))
					#bs.writeUInt(len(finalSdBytesList))
					
					didAppend = (didAppend or appendedPositions or appendedWeights or appendedIndices)
					if not isModded and (appendedPositions or appendedWeights or appendedIndices):
						print("Mesh will be appended to a new page: ", sm.name)
						if appendedPositions:
							print("	-exceeds the maximum vertex count of", sm.numVerts, "(has", str(len(writeMesh.positions)) + ")!")
						if appendedWeights:
							print("	-exceeds the maximum weight count of", sm.skinDesc.weightCount, "(has", str(fbxWeightCount) + ")!")
						if appendedIndices:
							print("	-exceeds the maximum poly count of", int(sm.numIndices/3), "(has", str(int(len(writeMesh.indices)/3)) + ")!")
							
				newPageStreams.append(wb)
					
			if isNoesisSplit:
				print("\nWARNING:	Duplicate mesh names detected! Check your FBX for naming or geometry issues. This pak may crash the game!\n")

		#Set all LODs except the last one to read as LOD0:
		if doWrite:# and not dialogOptions.doLODs:
			f.seek(source.geoOffset[0] + source.geoOffset[1] + 44)
			LODCount = f.readUInt()
			f.seek(32, 1)
			lodDescsOffset = source.readPointerFixup()
			f.seek(lodDescsOffset)
			lodDescs = []
			for a in range(LODCount):
				lodDescs.append(source.readPointerFixup())
			firstLODSubmeshOffs = readUIntAt(f, lodDescs[0] + 24)
			firstLODSubmeshCount = readUIntAt(f, lodDescs[0] + 4)
			for a in range(1, LODCount-1):
				bs.seek(lodDescs[a] + 24)
				bs.writeUInt64(firstLODSubmeshOffs)
				writeUIntAt(bs, lodDescs[a] + 4, firstLODSubmeshCount)
			
				
		#Embed image data
		path = rapi.getDirForFilePath(rapi.getOutputName())+rapi.getLocalFileName(rapi.getOutputName()).split(".", 1)[0]
		path2 = rapi.getDirForFilePath(injectMeshName)+rapi.getLocalFileName(injectMeshName).split(".", 1)[0]
		if noesis.optWasInvoked("-texfolder") and os.path.isdir(noesis.optGetArg("-texfolder")):
			path = noesis.optGetArg("-texfolder")
		print("\nChecking for textures to embed in:\n -", path, "\n -", path2)
		path = path2 if not os.path.isdir(path) else path
		#texInjectExt = ".tga" if dialogOptions.isTLOU2 else texoutExt
		
		if os.path.isdir(path):
			source.bs = bs
			vramPathDict = {}
			for hash, vramTuple in source.vrams.items():
				vramPathDict[vramTuple[1]] = (vramTuple[0], hash)
				
			for fileName in os.listdir(path):
				if os.path.isfile(os.path.join(path, fileName)) and fileName.count(texoutExt):
					vramTuple = vramPathDict.get(fileName)
					if vramTuple:
						print("\nEmbedding texture", fileName)
						source.writeVRAMImage(vramTuple[0], os.path.join(path, fileName))
						vramPathDict[fileName] = 0
					elif vramTuple != 0:
						print("Texture was found, but is not in the pak file\n	", fileName)
							
							
		if doWrite and didAppend:
			
			newPageDataAddrs = []
			numPages = len(newPageStreams)
			newPakPageHeaders = NoeBitStream()
			addAmt = 12*numPages
			orgPFixupPadAmt = 16 - ((pointerFixupTblOffs+8*12) % 16)
			pFixupPadAmt = 16 - ((pointerFixupTblOffs+8*12+addAmt+orgPFixupPadAmt) % 16) + 16 #pad to 16-bytes aligned, then add +16 bytes of new padding for 4294967295 modded marker / extra info
			writeUIntAt(bs, 28, pointerFixupTblOffs+12*numPages) #write pointerFixupTableOffset
			writeUIntAt(bs, 16, pageCt+numPages) # add new pages
			writeUIntAt(bs, pointerFixupTblOffs+4, readUIntAt(bs, pointerFixupTblOffs+4)+addAmt+pFixupPadAmt) #new dataOffset

			bs.seek(pointerFixupTblOffs)
			oldBytes = bs.readBytes(12) #copy old pointerFixup
			bs.seek(-12, 1)
			for i, wb in enumerate(newPageStreams):
				newPageDataAddrs.append(newPageDataAddr)
				while (newPageDataAddr+addAmt+pFixupPadAmt+wb.getSize()) % 16 != 12: #pad it out
					wb.writeByte(0)
				newPakPageHeaders.writeUInt(newPageDataAddr+addAmt+pFixupPadAmt) # new page offset
				newPakPageHeaders.writeUInt(wb.getSize()+20) # new page size
				newPakPageHeaders.writeUInt(owningIndex) # new package owning index
				newPageDataAddr = newPageDataAddr + wb.getSize()+20
				
			writeUIntAt(bs, 4, readUIntAt(bs, 4)+addAmt+pFixupPadAmt) #add to headerSz
			
			bs.seek(readUIntAt(bs, 20))
			for i in range(pageCt):
				bs.writeUInt(readUIntAt(bs, bs.tell())+addAmt+pFixupPadAmt) #add to each pageEntryOffset
				bs.seek(8, 1)
					
			if isModded:
				print("\nWARNING: File was previously injected. It is recommended to inject an unedited pak file\n")
			
			bs.seek(0)
			ns = NoeBitStream()
			ns.writeBytes(bs.readBytes(pointerFixupTblOffs))
			ns.writeBytes(newPakPageHeaders.getBuffer())
			ns.writeBytes(oldBytes)
			
			for i in range(7):
				ns.writeUInt64(0)
				ns.writeUInt(0)
			for i in range(pFixupPadAmt):
				ns.writeByte(0)
			if not isModded:
				writeUIntAt(ns, ns.tell()-pFixupPadAmt, 4294967295) #modded file marker
				writeUIntAt(ns, ns.tell()-pFixupPadAmt+4, pageCt) #original unmodded page count
			
			bs.seek(pointerFixupTblOffs+12*8)
			
			ns.writeBytes(bs.readBytes(newPageDataAddrs[0] - bs.tell()))
			
			for i, wb in enumerate(newPageStreams):
				ns.writeUInt64(16045690984833335023) #0xDEADBEEF
				ns.writeUInt(0) #74565) #unknown
				ns.writeUInt(wb.getSize()+20) #new size
				ns.writeUShort(owningIndex)
				ns.writeUShort(0)
				ns.writeBytes(wb.getBuffer())
				#if isModded:
				#	bs.seek(newPageDataAddrs[i]) #skip (delete) contents of page from previous injection
			ns.writeBytes(bs.readBytes(bs.getSize()-bs.tell()))
			
			bs.seek(0)
			bs.writeBytes(ns.getBuffer())
			
			print("Added", numPages, "new pages")
		
		if newBaseFile:
			print("Wrote new skeleton to", newBaseFile)
		
	return 1
	
