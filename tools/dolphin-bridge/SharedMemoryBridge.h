// Original MultiGalaxy1 Dolphin bridge glue.
// SPDX-License-Identifier: WTFPL
#pragma once

#include <array>
#include <cstdint>
#include <string>

namespace Core
{
class CPUThreadGuard;
}

namespace MultiGalaxy1
{
class SharedMemoryBridge
{
public:
  SharedMemoryBridge() = default;
  ~SharedMemoryBridge();
  SharedMemoryBridge(const SharedMemoryBridge&) = delete;
  SharedMemoryBridge& operator=(const SharedMemoryBridge&) = delete;

  // Called on the host thread before emulation starts. Performs all OS setup.
  bool Initialize(const std::string& game_id, uint16_t revision);
  // Called once per emulated field from Core::OnFrameEnd on the CPU thread.
  void PublishFrame(const Core::CPUThreadGuard& guard);
  void Shutdown();
  bool IsEnabled() const { return m_enabled; }

private:
  bool m_enabled = false;
  std::string m_game_id;
  uint16_t m_revision = 0;
  uint64_t m_frame = 0;
  void* m_mapping_handle = nullptr;
  void* m_owner_handle = nullptr;
  void* m_view = nullptr;
};
}  // namespace MultiGalaxy1
