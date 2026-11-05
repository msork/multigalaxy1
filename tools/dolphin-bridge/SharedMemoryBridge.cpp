// Original MultiGalaxy1 Dolphin bridge glue.
// SPDX-License-Identifier: WTFPL

#include "Core/SharedMemoryBridge.h"

#include <atomic>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <new>
#include <string_view>

#include "Common/Logging/Log.h"
#include "Core/ConfigManager.h"
#include "Core/Core.h"
#include "Core/PowerPC/MMU.h"
#include "Core/System.h"

#ifdef _WIN32
#include <windows.h>
#endif

namespace MultiGalaxy1
{
namespace
{
constexpr uint64_t SNAPSHOT_MAGIC = UINT64_C(0x454744524231474d);  // "MG1BRDGE" little-endian
constexpr uint32_t SNAPSHOT_ABI = 1;
constexpr uint32_t PAYLOAD_SCHEMA = 1;
constexpr size_t MAX_PAYLOAD = 128;
constexpr size_t PAYLOAD_SIZE = 48;
constexpr uint32_t IDENTITY_VALID = 1;
constexpr uint32_t RAM_SAMPLE_VALID = 2;
constexpr uint32_t MEM1_BASE = 0x80000000;

// This layout mirrors crates/multigalaxy-cli/src/shared_snapshot.rs. Raw
// integer storage plus atomic_ref keeps the cross-language layout explicit.
struct alignas(64) SharedSnapshot
{
  uint64_t magic;
  uint32_t version;
  uint32_t reserved;
  uint64_t sequence;
  uint32_t payload_len;
  uint32_t padding;
  uint8_t payload[MAX_PAYLOAD];
};
static_assert(offsetof(SharedSnapshot, magic) == 0);
static_assert(offsetof(SharedSnapshot, version) == 8);
static_assert(offsetof(SharedSnapshot, sequence) == 16);
static_assert(offsetof(SharedSnapshot, payload_len) == 24);
static_assert(offsetof(SharedSnapshot, payload) == 32);
static_assert(sizeof(SharedSnapshot) == 192);
static_assert(std::atomic_ref<uint64_t>::is_always_lock_free);
static_assert(std::atomic_ref<uint32_t>::is_always_lock_free);
static_assert(std::atomic_ref<uint8_t>::is_always_lock_free);

void StoreU32(uint8_t* out, uint32_t value)
{
  for (unsigned i = 0; i < 4; ++i)
    out[i] = static_cast<uint8_t>(value >> (i * 8));
}

void StoreU64(uint8_t* out, uint64_t value)
{
  for (unsigned i = 0; i < 8; ++i)
    out[i] = static_cast<uint8_t>(value >> (i * 8));
}

#ifdef _WIN32
std::wstring MakeObjectName(const wchar_t* suffix, std::string_view name)
{
  std::wstring wide;
  wide.reserve(name.size() + 48);
  wide = L"Local\\MultiGalaxy1-";
  for (const char c : name)
    wide.push_back(static_cast<wchar_t>(static_cast<unsigned char>(c)));
  wide += suffix;
  return wide;
}
#endif
}  // namespace

SharedMemoryBridge::~SharedMemoryBridge()
{
  Shutdown();
}

bool SharedMemoryBridge::Initialize(const std::string& game_id, const uint16_t revision)
{
  Shutdown();
  const char* configured_name = std::getenv("MULTIGALAXY_BRIDGE_NAME");
  if (!configured_name || !*configured_name)
    return false;  // No mapping or MMU work; OnFrameEnd only sees a false enabled flag.

  if (game_id != "RMGP01" || revision != 0)
  {
    ERROR_LOG_FMT(CORE, "MultiGalaxy bridge disabled: expected PAL RMGP01 Rev 0, got {} Rev {}",
                  game_id, revision);
    return false;
  }

#ifdef _WIN32
  const std::string_view name(configured_name);
  if (name.size() > 48 || name.find_first_not_of(
                              "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-") !=
                              std::string_view::npos)
  {
    ERROR_LOG_FMT(CORE, "MultiGalaxy bridge disabled: invalid mapping name");
    return false;
  }

  const std::wstring owner_name = MakeObjectName(L"-owner", name);
  HANDLE owner = CreateEventW(nullptr, TRUE, FALSE, owner_name.c_str());
  if (!owner || GetLastError() == ERROR_ALREADY_EXISTS)
  {
    if (owner)
      CloseHandle(owner);
    ERROR_LOG_FMT(CORE, "MultiGalaxy bridge disabled: another publisher owns mapping {}", name);
    return false;
  }

  const std::wstring mapping_name = MakeObjectName(L"", name);
  HANDLE mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
                                      sizeof(SharedSnapshot), mapping_name.c_str());
  if (!mapping || GetLastError() == ERROR_ALREADY_EXISTS)
  {
    if (mapping)
      CloseHandle(mapping);
    CloseHandle(owner);
    ERROR_LOG_FMT(CORE, "MultiGalaxy bridge disabled: mapping already exists or cannot be created");
    return false;
  }
  void* view = MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(SharedSnapshot));
  if (!view)
  {
    CloseHandle(mapping);
    CloseHandle(owner);
    ERROR_LOG_FMT(CORE, "MultiGalaxy bridge disabled: MapViewOfFile failed ({})", GetLastError());
    return false;
  }

  auto* snapshot = ::new (view) SharedSnapshot{};
  std::atomic_ref(snapshot->version).store(SNAPSHOT_ABI, std::memory_order_seq_cst);
  std::atomic_ref(snapshot->magic).store(SNAPSHOT_MAGIC, std::memory_order_seq_cst);

  m_game_id = game_id;
  m_revision = revision;
  m_frame = 0;
  m_mapping_handle = mapping;
  m_owner_handle = owner;
  m_view = view;
  m_enabled = true;
  INFO_LOG_FMT(CORE, "MultiGalaxy bridge ABI {} enabled for {} Rev {}", SNAPSHOT_ABI, game_id,
               revision);
  return true;
#else
  ERROR_LOG_FMT(CORE, "MultiGalaxy bridge is not implemented for this operating system");
  return false;
#endif
}

void SharedMemoryBridge::PublishFrame(const Core::CPUThreadGuard& guard)
{
#ifdef _WIN32
  if (!m_enabled || !m_view)
    return;

  auto* snapshot = static_cast<SharedSnapshot*>(m_view);
  if (std::atomic_ref(snapshot->magic).load(std::memory_order_seq_cst) != SNAPSHOT_MAGIC ||
      std::atomic_ref(snapshot->version).load(std::memory_order_seq_cst) != SNAPSHOT_ABI)
  {
    m_enabled = false;
    ERROR_LOG_FMT(CORE, "MultiGalaxy bridge disabled after shared-memory ABI changed");
    return;
  }

  uint32_t flags = IDENTITY_VALID;
  uint32_t sample = 0;
  if (PowerPC::MMU::HostIsRAMAddress(guard, MEM1_BASE,
                                     PowerPC::RequestedAddressSpace::Effective))
  {
    const auto result = PowerPC::MMU::HostTryRead<uint32_t>(
        guard, MEM1_BASE, PowerPC::RequestedAddressSpace::Effective);
    if (result)
    {
      sample = result->value;
      flags |= RAM_SAMPLE_VALID;
    }
  }

  uint8_t payload[PAYLOAD_SIZE]{};
  StoreU32(payload + 0, PAYLOAD_SCHEMA);
  StoreU32(payload + 4, m_revision);
  std::memcpy(payload + 8, m_game_id.data(), 6);
  StoreU32(payload + 16, flags);
  const uint64_t heartbeat = ++m_frame;
  StoreU64(payload + 24, m_frame);
  StoreU64(payload + 32, heartbeat);
  StoreU32(payload + 40, MEM1_BASE);
  StoreU32(payload + 44, sample);

  std::atomic_ref<uint64_t> sequence(snapshot->sequence);
  const uint64_t even = sequence.load(std::memory_order_relaxed);
  if (even & 1)
  {
    m_enabled = false;
    ERROR_LOG_FMT(CORE, "MultiGalaxy bridge disabled after invalid sequence state");
    return;
  }
  sequence.store(even + 1, std::memory_order_seq_cst);
  std::atomic_thread_fence(std::memory_order_seq_cst);
  std::atomic_ref(snapshot->payload_len).store(PAYLOAD_SIZE, std::memory_order_seq_cst);
  for (size_t i = 0; i < PAYLOAD_SIZE; ++i)
    std::atomic_ref(snapshot->payload[i]).store(payload[i], std::memory_order_seq_cst);
  std::atomic_thread_fence(std::memory_order_seq_cst);
  sequence.store(even + 2, std::memory_order_seq_cst);
#else
  (void)guard;
#endif
}

void SharedMemoryBridge::Shutdown()
{
#ifdef _WIN32
  if (m_view)
    UnmapViewOfFile(m_view);
  if (m_mapping_handle)
    CloseHandle(static_cast<HANDLE>(m_mapping_handle));
  if (m_owner_handle)
    CloseHandle(static_cast<HANDLE>(m_owner_handle));
#endif
  m_view = nullptr;
  m_mapping_handle = nullptr;
  m_owner_handle = nullptr;
  m_enabled = false;
  m_game_id.clear();
  m_revision = 0;
  m_frame = 0;
}
}  // namespace MultiGalaxy1
