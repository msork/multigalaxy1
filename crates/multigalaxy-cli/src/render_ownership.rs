//! Synthetic host-side ownership model for a possible shared-resource J3D clone.
//!
//! This module is not connected to Dolphin or game memory. Its allocator and
//! model descriptor are deliberately host abstractions, not Wii/JRK ABI claims.

#![allow(dead_code)] // Deliberately exercised offline, not wired into runtime code.

use std::cell::Cell;
use std::rc::Rc;
use std::sync::Arc;

const MAX_MODEL_ITEMS: usize = 8192;
const MAX_SYNTHETIC_BYTES: usize = 64 * 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum AllocationKind {
    ModelInstance,
    MatrixBuffer,
    AnimationMatrices,
    WeightMatrices,
    DrawPointerTables,
    DrawMatrices,
    NormalMatrices,
    BumpMatrices,
    ExtraViewMatrices,
    ShapePackets,
    MaterialPackets,
    ModelXTransient,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ModelDescriptor {
    resource_id: u64,
    scene_generation: u64,
    joint_count: usize,
    weight_matrix_count: usize,
    draw_matrix_count: usize,
    shape_count: usize,
    material_count: usize,
    bump_matrix_count: usize,
}

impl ModelDescriptor {
    fn validate(self) -> Result<(), OwnershipError> {
        if self.resource_id == 0 || self.scene_generation == 0 {
            return Err(OwnershipError::InvalidDescriptor("missing identity"));
        }
        for (name, count) in [
            ("joints", self.joint_count),
            ("draw matrices", self.draw_matrix_count),
            ("shapes", self.shape_count),
            ("materials", self.material_count),
        ] {
            if count == 0 || count > MAX_MODEL_ITEMS {
                return Err(OwnershipError::InvalidCount { name, count });
            }
        }
        for (name, count) in [
            ("weight matrices", self.weight_matrix_count),
            ("bump matrices", self.bump_matrix_count),
        ] {
            if count > MAX_MODEL_ITEMS {
                return Err(OwnershipError::InvalidCount { name, count });
            }
        }
        Ok(())
    }

    fn allocation_plan(self) -> Result<Vec<(AllocationKind, usize)>, OwnershipError> {
        self.validate()?;
        // Byte totals are synthetic host-test accounting only. In particular,
        // 0x300/0x40/32 are not PAL object sizes or Dolphin allocation requests.
        let matrix_bytes = |count: usize, bytes_each: usize, views: usize| {
            count
                .checked_mul(bytes_each)
                .and_then(|n| n.checked_mul(views))
                .ok_or(OwnershipError::SizeOverflow)
        };
        let packet_bytes = |count: usize| count.checked_mul(32).ok_or(OwnershipError::SizeOverflow);
        let draw_bytes = matrix_bytes(self.draw_matrix_count, 48, 2)?;
        let normal_bytes = matrix_bytes(self.draw_matrix_count, 36, 2)?;
        let mut plan = vec![
            (AllocationKind::ModelInstance, 0x300),
            (AllocationKind::MatrixBuffer, 0x40),
            (
                AllocationKind::AnimationMatrices,
                matrix_bytes(self.joint_count, 48, 1)?,
            ),
            (
                AllocationKind::DrawPointerTables,
                self.draw_matrix_count
                    .checked_mul(2 * std::mem::size_of::<usize>() * 2)
                    .ok_or(OwnershipError::SizeOverflow)?,
            ),
            (AllocationKind::DrawMatrices, draw_bytes),
            (AllocationKind::NormalMatrices, normal_bytes),
            (
                AllocationKind::ExtraViewMatrices,
                matrix_bytes(self.draw_matrix_count, 48, 1)?,
            ),
            (
                AllocationKind::ShapePackets,
                packet_bytes(self.shape_count)?,
            ),
            (
                AllocationKind::MaterialPackets,
                packet_bytes(self.material_count)?,
            ),
            (AllocationKind::ModelXTransient, 0x100),
        ];
        if self.weight_matrix_count != 0 {
            plan.push((
                AllocationKind::WeightMatrices,
                matrix_bytes(self.weight_matrix_count, 48, 1)?,
            ));
        }
        if self.bump_matrix_count != 0 {
            plan.push((
                AllocationKind::BumpMatrices,
                matrix_bytes(self.bump_matrix_count, 48, 1)?,
            ));
        }
        let total = plan.iter().try_fold(0usize, |sum, (_, bytes)| {
            sum.checked_add(*bytes).ok_or(OwnershipError::SizeOverflow)
        })?;
        if total > MAX_SYNTHETIC_BYTES {
            return Err(OwnershipError::AllocationLimit(total));
        }
        Ok(plan)
    }
}

/// Immutable resource identity and model counts shared by synthetic instances.
#[derive(Debug)]
struct SharedModelData {
    descriptor: ModelDescriptor,
}

#[derive(Debug, Clone, Default, PartialEq)]
struct Matrix([[f32; 4]; 3]);

#[derive(Debug, Clone, Default, PartialEq)]
struct PacketState {
    matrix_index: usize,
    mutable_cookie: u32,
}

#[derive(Debug, Default)]
struct LedgerInner {
    live_allocations: Cell<usize>,
    live_bytes: Cell<usize>,
}

#[derive(Debug, Clone, Default)]
struct AllocationLedger(Rc<LedgerInner>);

impl AllocationLedger {
    fn allocate(
        &self,
        kind: AllocationKind,
        bytes: usize,
    ) -> Result<AllocationLease, OwnershipError> {
        if bytes == 0 {
            return Err(OwnershipError::InvalidAllocation(kind));
        }
        let next_bytes = self
            .0
            .live_bytes
            .get()
            .checked_add(bytes)
            .ok_or(OwnershipError::SizeOverflow)?;
        if next_bytes > MAX_SYNTHETIC_BYTES {
            return Err(OwnershipError::AllocationLimit(next_bytes));
        }
        self.0
            .live_allocations
            .set(self.0.live_allocations.get() + 1);
        self.0.live_bytes.set(next_bytes);
        Ok(AllocationLease {
            ledger: self.clone(),
            kind,
            bytes,
        })
    }

    fn live(&self) -> (usize, usize) {
        (self.0.live_allocations.get(), self.0.live_bytes.get())
    }
}

#[derive(Debug)]
struct AllocationLease {
    ledger: AllocationLedger,
    kind: AllocationKind,
    bytes: usize,
}

impl Drop for AllocationLease {
    fn drop(&mut self) {
        let _allocation_kind = self.kind;
        self.ledger
            .0
            .live_allocations
            .set(self.ledger.0.live_allocations.get() - 1);
        self.ledger
            .0
            .live_bytes
            .set(self.ledger.0.live_bytes.get() - self.bytes);
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum InjectedFailure {
    BeforeAllocation(usize),
    DuringInitialization,
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum OwnershipError {
    InvalidDescriptor(&'static str),
    InvalidCount { name: &'static str, count: usize },
    InvalidAllocation(AllocationKind),
    SizeOverflow,
    AllocationLimit(usize),
    HostAllocation,
    Injected(InjectedFailure),
}

#[derive(Debug)]
struct RenderInstance {
    shared: Arc<SharedModelData>,
    scene_generation: u64,
    leases: Vec<AllocationLease>,
    animation_matrices: Vec<Matrix>,
    weight_matrices: Vec<Matrix>,
    draw_views: [Vec<Matrix>; 2],
    normal_views: [Vec<Matrix>; 2],
    bump_matrices: Vec<Matrix>,
    extra_view_matrices: Vec<Matrix>,
    shape_packets: Vec<PacketState>,
    material_packets: Vec<PacketState>,
    initialized: bool,
}

fn zeroed<T: Clone + Default>(count: usize) -> Result<Vec<T>, OwnershipError> {
    let mut values = Vec::new();
    values
        .try_reserve_exact(count)
        .map_err(|_| OwnershipError::HostAllocation)?;
    values.resize(count, T::default());
    Ok(values)
}

impl RenderInstance {
    fn build(
        shared: Arc<SharedModelData>,
        ledger: &AllocationLedger,
        fail: Option<InjectedFailure>,
    ) -> Result<Self, OwnershipError> {
        let descriptor = shared.descriptor;
        let plan = descriptor.allocation_plan()?;
        let mut leases = Vec::new();
        leases
            .try_reserve_exact(plan.len())
            .map_err(|_| OwnershipError::HostAllocation)?;
        for (index, (kind, bytes)) in plan.iter().copied().enumerate() {
            if fail == Some(InjectedFailure::BeforeAllocation(index)) {
                return Err(OwnershipError::Injected(InjectedFailure::BeforeAllocation(
                    index,
                )));
            }
            leases.push(ledger.allocate(kind, bytes)?);
        }
        if fail == Some(InjectedFailure::DuringInitialization) {
            return Err(OwnershipError::Injected(
                InjectedFailure::DuringInitialization,
            ));
        }

        let instance = Self {
            shared,
            scene_generation: descriptor.scene_generation,
            leases,
            animation_matrices: zeroed(descriptor.joint_count)?,
            weight_matrices: zeroed(descriptor.weight_matrix_count)?,
            draw_views: [
                zeroed(descriptor.draw_matrix_count)?,
                zeroed(descriptor.draw_matrix_count)?,
            ],
            normal_views: [
                zeroed(descriptor.draw_matrix_count)?,
                zeroed(descriptor.draw_matrix_count)?,
            ],
            bump_matrices: zeroed(descriptor.bump_matrix_count)?,
            extra_view_matrices: zeroed(descriptor.draw_matrix_count)?,
            shape_packets: zeroed(descriptor.shape_count)?,
            material_packets: zeroed(descriptor.material_count)?,
            initialized: true,
        };
        debug_assert_eq!(
            instance.scene_generation,
            instance.shared.descriptor.scene_generation
        );
        debug_assert_eq!(instance.leases.len(), plan.len());
        Ok(instance)
    }

    fn set_draw_component(
        &mut self,
        view: usize,
        matrix: usize,
        row: usize,
        column: usize,
        value: f32,
    ) -> Result<(), OwnershipError> {
        if !self.initialized
            || self.scene_generation != self.shared.descriptor.scene_generation
            || !value.is_finite()
        {
            return Err(OwnershipError::InvalidDescriptor("instance not live"));
        }
        let cell = self
            .draw_views
            .get_mut(view)
            .and_then(|view| view.get_mut(matrix))
            .and_then(|matrix| matrix.0.get_mut(row))
            .and_then(|row| row.get_mut(column))
            .ok_or(OwnershipError::InvalidDescriptor(
                "matrix index out of bounds",
            ))?;
        *cell = value;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn descriptor() -> ModelDescriptor {
        ModelDescriptor {
            resource_id: 0x4d4152494f,
            scene_generation: 7,
            joint_count: 32,
            weight_matrix_count: 8,
            draw_matrix_count: 48,
            shape_count: 24,
            material_count: 16,
            bump_matrix_count: 4,
        }
    }

    fn shared() -> Arc<SharedModelData> {
        Arc::new(SharedModelData {
            descriptor: descriptor(),
        })
    }

    #[test]
    fn instances_share_only_immutable_asset_and_isolate_mutable_state() {
        let ledger = AllocationLedger::default();
        let asset = shared();
        let original = RenderInstance::build(asset.clone(), &ledger, None).unwrap();
        let mut remote = RenderInstance::build(asset.clone(), &ledger, None).unwrap();
        assert!(Arc::ptr_eq(&original.shared, &remote.shared));
        assert!(!std::ptr::eq(
            original.draw_views[0].as_ptr(),
            remote.draw_views[0].as_ptr()
        ));
        assert!(!std::ptr::eq(
            original.shape_packets.as_ptr(),
            remote.shape_packets.as_ptr()
        ));
        assert_eq!(remote.draw_views[0][0], Matrix::default());
        assert_eq!(remote.shape_packets[0], PacketState::default());

        remote.set_draw_component(0, 0, 0, 3, 125.0).unwrap();
        remote.animation_matrices[0].0[0][0] = 42.0;
        remote.weight_matrices[0].0[0][0] = 13.0;
        remote.normal_views[0][0].0[0][0] = 11.0;
        remote.bump_matrices[0].0[0][0] = 9.0;
        remote.extra_view_matrices[0].0[0][0] = 7.0;
        remote.shape_packets[0].mutable_cookie = 0xfeed_beef;
        remote.material_packets[0].mutable_cookie = 0xcafe_babe;
        assert_eq!(original.draw_views[0][0], Matrix::default());
        assert_eq!(original.animation_matrices[0], Matrix::default());
        assert_eq!(original.weight_matrices[0], Matrix::default());
        assert_eq!(original.normal_views[0][0], Matrix::default());
        assert_eq!(original.bump_matrices[0], Matrix::default());
        assert_eq!(original.extra_view_matrices[0], Matrix::default());
        assert_eq!(original.shape_packets[0], PacketState::default());
        assert_eq!(original.material_packets[0], PacketState::default());
        assert_eq!(original.shared.descriptor, descriptor());
    }

    #[test]
    fn successful_cleanup_releases_every_synthetic_allocation() {
        let ledger = AllocationLedger::default();
        let instance = RenderInstance::build(shared(), &ledger, None).unwrap();
        assert!(ledger.live().0 > 0);
        assert!(ledger.live().1 > 0);
        drop(instance);
        assert_eq!(ledger.live(), (0, 0));
    }

    #[test]
    fn every_allocation_failure_rolls_back_prior_allocations() {
        let ledger = AllocationLedger::default();
        let count = descriptor().allocation_plan().unwrap().len();
        for index in 0..count {
            let result = RenderInstance::build(
                shared(),
                &ledger,
                Some(InjectedFailure::BeforeAllocation(index)),
            );
            assert!(matches!(result, Err(OwnershipError::Injected(_))));
            assert_eq!(ledger.live(), (0, 0), "leaked after allocation {index}");
        }
    }

    #[test]
    fn initialization_failure_rolls_back_the_complete_allocation_plan() {
        let ledger = AllocationLedger::default();
        let result = RenderInstance::build(
            shared(),
            &ledger,
            Some(InjectedFailure::DuringInitialization),
        );
        assert_eq!(
            result.unwrap_err(),
            OwnershipError::Injected(InjectedFailure::DuringInitialization)
        );
        assert_eq!(ledger.live(), (0, 0));
    }

    #[test]
    fn malformed_descriptor_fails_before_any_allocation() {
        let ledger = AllocationLedger::default();
        let mut invalid = descriptor();
        invalid.draw_matrix_count = usize::MAX;
        let asset = Arc::new(SharedModelData {
            descriptor: invalid,
        });
        assert!(RenderInstance::build(asset, &ledger, None).is_err());
        assert_eq!(ledger.live(), (0, 0));
    }

    #[test]
    fn optional_weight_and_bump_buffers_can_be_absent() {
        let ledger = AllocationLedger::default();
        let mut model = descriptor();
        model.weight_matrix_count = 0;
        model.bump_matrix_count = 0;
        let instance = RenderInstance::build(
            Arc::new(SharedModelData { descriptor: model }),
            &ledger,
            None,
        )
        .unwrap();
        assert!(instance.weight_matrices.is_empty());
        assert!(instance.bump_matrices.is_empty());
        drop(instance);
        assert_eq!(ledger.live(), (0, 0));
    }
}
