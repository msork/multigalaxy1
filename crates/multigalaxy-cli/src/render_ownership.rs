//! Synthetic host-side ownership model for a possible shared-resource J3D clone.
//!
//! This module is not connected to Dolphin or game memory. Its scene arena,
//! allocator, and descriptors are host abstractions, not Wii/JRK ABI claims.

#![allow(dead_code)] // Deliberately exercised offline, not wired into runtime code.

use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::sync::Arc;

const MAX_MODEL_ITEMS: usize = 8192;
const MAX_SYNTHETIC_BYTES: usize = 64 * 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct HeapId(u64);

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
    heap_id: HeapId,
    joint_count: usize,
    weight_matrix_count: usize,
    draw_matrix_count: usize,
    shape_count: usize,
    material_count: usize,
    bump_matrix_count: usize,
}

impl ModelDescriptor {
    fn validate(self) -> Result<(), OwnershipError> {
        if self.resource_id == 0 || self.scene_generation == 0 || self.heap_id.0 == 0 {
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
        // These byte totals are synthetic host accounting only. Values such as
        // 0x300/0x40/32 are not PAL object sizes or Dolphin allocation requests.
        let matrix_bytes = |count: usize, bytes_each: usize, views: usize| {
            count
                .checked_mul(bytes_each)
                .and_then(|n| n.checked_mul(views))
                .ok_or(OwnershipError::SizeOverflow)
        };
        let packet_bytes = |count: usize| count.checked_mul(32).ok_or(OwnershipError::SizeOverflow);
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
            (
                AllocationKind::DrawMatrices,
                matrix_bytes(self.draw_matrix_count, 48, 2)?,
            ),
            (
                AllocationKind::NormalMatrices,
                matrix_bytes(self.draw_matrix_count, 36, 2)?,
            ),
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

/// Resource identity is immutable in this model and kept alive by each instance.
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
        heap_id: HeapId,
    ) -> Result<AllocationLease, OwnershipError> {
        if bytes == 0 || heap_id.0 == 0 {
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
            heap_id,
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
    heap_id: HeapId,
}

impl Drop for AllocationLease {
    fn drop(&mut self) {
        let _allocation_owner = (self.kind, self.heap_id);
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

#[derive(Debug)]
struct SceneHeapState {
    heap_id: HeapId,
    generation: u64,
    alive: bool,
    attached_instances: usize,
    allocations: Vec<AllocationLease>,
    ledger: AllocationLedger,
}

#[derive(Debug, Clone)]
struct SceneHeapArena(Rc<RefCell<SceneHeapState>>);

impl SceneHeapArena {
    fn new(heap_id: HeapId, generation: u64, ledger: AllocationLedger) -> Self {
        Self(Rc::new(RefCell::new(SceneHeapState {
            heap_id,
            generation,
            alive: true,
            attached_instances: 0,
            allocations: Vec::new(),
            ledger,
        })))
    }

    fn checkpoint(&self) -> usize {
        self.0.borrow().allocations.len()
    }

    fn allocate(&self, kind: AllocationKind, bytes: usize) -> Result<(), OwnershipError> {
        let mut state = self.0.borrow_mut();
        if !state.alive {
            return Err(OwnershipError::SceneHeapDestroyed);
        }
        let lease = state.ledger.allocate(kind, bytes, state.heap_id)?;
        state
            .allocations
            .try_reserve(1)
            .map_err(|_| OwnershipError::HostAllocation)?;
        state.allocations.push(lease);
        Ok(())
    }

    fn rollback(&self, checkpoint: usize) {
        self.0.borrow_mut().allocations.truncate(checkpoint);
    }

    fn attach_instance(&self) {
        self.0.borrow_mut().attached_instances += 1;
    }

    fn detach_instance(&self) {
        let mut state = self.0.borrow_mut();
        state.attached_instances -= 1;
    }

    fn is_live(&self, heap_id: HeapId, generation: u64) -> bool {
        let state = self.0.borrow();
        state.alive && state.heap_id == heap_id && state.generation == generation
    }

    fn destroy(&self) -> Result<(), OwnershipError> {
        let mut state = self.0.borrow_mut();
        if state.attached_instances != 0 {
            return Err(OwnershipError::OutstandingInstances(
                state.attached_instances,
            ));
        }
        state.alive = false;
        state.allocations.clear();
        Ok(())
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
    SceneHeapDestroyed,
    OutstandingInstances(usize),
    WrongCurrentHeap { owner: HeapId, current: HeapId },
}

#[derive(Debug)]
struct RenderInstance {
    shared: Arc<SharedModelData>,
    arena: SceneHeapArena,
    scene_generation: u64,
    heap_id: HeapId,
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

impl Drop for RenderInstance {
    fn drop(&mut self) {
        self.arena.detach_instance();
    }
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
        arena: SceneHeapArena,
        fail: Option<InjectedFailure>,
    ) -> Result<Self, OwnershipError> {
        let checkpoint = arena.checkpoint();
        let result = Self::build_inner(shared, arena.clone(), fail);
        if result.is_err() {
            arena.rollback(checkpoint);
        }
        result
    }

    fn build_inner(
        shared: Arc<SharedModelData>,
        arena: SceneHeapArena,
        fail: Option<InjectedFailure>,
    ) -> Result<Self, OwnershipError> {
        let descriptor = shared.descriptor;
        let plan = descriptor.allocation_plan()?;
        let state = arena.0.borrow();
        if !state.alive {
            return Err(OwnershipError::SceneHeapDestroyed);
        }
        if state.heap_id != descriptor.heap_id || state.generation != descriptor.scene_generation {
            return Err(OwnershipError::InvalidDescriptor(
                "resource, scene, and heap identities do not match",
            ));
        }
        drop(state);

        for (index, (kind, bytes)) in plan.iter().copied().enumerate() {
            if fail == Some(InjectedFailure::BeforeAllocation(index)) {
                return Err(OwnershipError::Injected(InjectedFailure::BeforeAllocation(
                    index,
                )));
            }
            arena.allocate(kind, bytes)?;
        }
        if fail == Some(InjectedFailure::DuringInitialization) {
            return Err(OwnershipError::Injected(
                InjectedFailure::DuringInitialization,
            ));
        }

        let instance = Self {
            shared,
            arena: arena.clone(),
            scene_generation: descriptor.scene_generation,
            heap_id: descriptor.heap_id,
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
        debug_assert_eq!(instance.heap_id, instance.shared.descriptor.heap_id);
        instance.arena.attach_instance();
        Ok(instance)
    }

    fn validate_explicit_delete_heap(&self, current_heap: HeapId) -> Result<(), OwnershipError> {
        if !self.arena.is_live(self.heap_id, self.scene_generation) {
            return Err(OwnershipError::SceneHeapDestroyed);
        }
        if current_heap != self.heap_id {
            return Err(OwnershipError::WrongCurrentHeap {
                owner: self.heap_id,
                current: current_heap,
            });
        }
        Ok(())
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
            || !self.arena.is_live(self.heap_id, self.scene_generation)
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

    const SCENE_HEAP: HeapId = HeapId(0x51);

    fn descriptor() -> ModelDescriptor {
        ModelDescriptor {
            resource_id: 0x4d4152494f,
            scene_generation: 7,
            heap_id: SCENE_HEAP,
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

    fn arena(ledger: AllocationLedger) -> SceneHeapArena {
        SceneHeapArena::new(SCENE_HEAP, 7, ledger)
    }

    #[test]
    fn shared_resources_and_all_mutable_instance_fields_are_isolated() {
        let ledger = AllocationLedger::default();
        let arena = arena(ledger.clone());
        let asset = shared();
        let original = RenderInstance::build(asset.clone(), arena.clone(), None).unwrap();
        let mut remote = RenderInstance::build(asset.clone(), arena.clone(), None).unwrap();
        assert!(Arc::ptr_eq(&original.shared, &remote.shared));
        assert!(!std::ptr::eq(
            original.draw_views[0].as_ptr(),
            remote.draw_views[0].as_ptr()
        ));
        assert!(!std::ptr::eq(
            original.shape_packets.as_ptr(),
            remote.shape_packets.as_ptr()
        ));
        assert!(!std::ptr::eq(
            original.material_packets.as_ptr(),
            remote.material_packets.as_ptr()
        ));

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
        drop((original, remote));
        arena.destroy().unwrap();
        assert_eq!(ledger.live(), (0, 0));
    }

    #[test]
    fn model_instance_drop_does_not_free_scene_heap_arrays() {
        let ledger = AllocationLedger::default();
        let arena = arena(ledger.clone());
        let instance = RenderInstance::build(shared(), arena.clone(), None).unwrap();
        let allocated = ledger.live();
        assert!(allocated.0 > 0);
        drop(instance);
        assert_eq!(ledger.live(), allocated);
        arena.destroy().unwrap();
        assert_eq!(ledger.live(), (0, 0));
    }

    #[test]
    fn scene_heap_cannot_be_destroyed_while_instances_are_attached() {
        let ledger = AllocationLedger::default();
        let arena = arena(ledger.clone());
        let instance = RenderInstance::build(shared(), arena.clone(), None).unwrap();
        assert_eq!(
            arena.destroy(),
            Err(OwnershipError::OutstandingInstances(1))
        );
        assert!(instance.validate_explicit_delete_heap(SCENE_HEAP).is_ok());
        drop(instance);
        arena.destroy().unwrap();
        assert_eq!(ledger.live(), (0, 0));
    }

    #[test]
    fn explicit_delete_rejects_a_different_current_heap() {
        let ledger = AllocationLedger::default();
        let arena = arena(ledger.clone());
        let instance = RenderInstance::build(shared(), arena.clone(), None).unwrap();
        assert_eq!(
            instance.validate_explicit_delete_heap(HeapId(0x52)),
            Err(OwnershipError::WrongCurrentHeap {
                owner: SCENE_HEAP,
                current: HeapId(0x52),
            })
        );
        assert!(instance.validate_explicit_delete_heap(SCENE_HEAP).is_ok());
        drop(instance);
        arena.destroy().unwrap();
        assert_eq!(ledger.live(), (0, 0));
    }

    #[test]
    fn shared_model_data_borrow_remains_live_until_instance_is_detached() {
        let ledger = AllocationLedger::default();
        let arena = arena(ledger.clone());
        let asset = shared();
        let weak = Arc::downgrade(&asset);
        let instance = RenderInstance::build(asset.clone(), arena.clone(), None).unwrap();
        drop(asset);
        assert!(weak.upgrade().is_some());
        drop(instance);
        assert!(weak.upgrade().is_none());
        arena.destroy().unwrap();
        assert_eq!(ledger.live(), (0, 0));
    }

    #[test]
    fn every_allocation_failure_rolls_back_to_the_heap_checkpoint() {
        let ledger = AllocationLedger::default();
        let arena = arena(ledger.clone());
        arena.allocate(AllocationKind::ModelInstance, 16).unwrap();
        let baseline = ledger.live();
        let count = descriptor().allocation_plan().unwrap().len();
        for index in 0..count {
            let result = RenderInstance::build(
                shared(),
                arena.clone(),
                Some(InjectedFailure::BeforeAllocation(index)),
            );
            assert!(matches!(result, Err(OwnershipError::Injected(_))));
            assert_eq!(ledger.live(), baseline, "leaked after allocation {index}");
        }
        arena.destroy().unwrap();
    }

    #[test]
    fn initialization_failure_rolls_back_the_complete_heap_transaction() {
        let ledger = AllocationLedger::default();
        let arena = arena(ledger.clone());
        arena.allocate(AllocationKind::ModelInstance, 16).unwrap();
        let result = RenderInstance::build(
            shared(),
            arena.clone(),
            Some(InjectedFailure::DuringInitialization),
        );
        assert_eq!(
            result.unwrap_err(),
            OwnershipError::Injected(InjectedFailure::DuringInitialization)
        );
        assert_eq!(ledger.live(), (1, 16));
        arena.destroy().unwrap();
    }

    #[test]
    fn invalid_or_wrong_scene_heap_identity_fails_before_allocation() {
        let ledger = AllocationLedger::default();
        let arena = arena(ledger.clone());
        let mut invalid = descriptor();
        invalid.draw_matrix_count = usize::MAX;
        assert!(RenderInstance::build(
            Arc::new(SharedModelData {
                descriptor: invalid
            }),
            arena.clone(),
            None
        )
        .is_err());

        let mut wrong_generation = descriptor();
        wrong_generation.scene_generation += 1;
        assert!(RenderInstance::build(
            Arc::new(SharedModelData {
                descriptor: wrong_generation
            }),
            arena.clone(),
            None
        )
        .is_err());
        assert_eq!(ledger.live(), (0, 0));
        arena.destroy().unwrap();
    }

    #[test]
    fn optional_weight_and_bump_buffers_can_be_absent() {
        let ledger = AllocationLedger::default();
        let arena = arena(ledger.clone());
        let mut model = descriptor();
        model.weight_matrix_count = 0;
        model.bump_matrix_count = 0;
        let instance = RenderInstance::build(
            Arc::new(SharedModelData { descriptor: model }),
            arena.clone(),
            None,
        )
        .unwrap();
        assert!(instance.weight_matrices.is_empty());
        assert!(instance.bump_matrices.is_empty());
        drop(instance);
        arena.destroy().unwrap();
        assert_eq!(ledger.live(), (0, 0));
    }
}
