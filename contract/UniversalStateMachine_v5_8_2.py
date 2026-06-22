from algopy import (
    ARC4Contract,
    Account,
    BoxRef,
    Bytes,
    Global,
    OpUpFeeSource,
    Txn,
    UInt64,
    arc4,
    ensure_budget,
    gtxn,
    itxn,
    op,
    subroutine,
)


BOX_FLAT_MBR = 2_500
BOX_BYTE_MBR = 400

# Keep each frequently accessed box value at or below one 1 KiB box-I/O unit.
# This also keeps ARC-4 string returns safely below the 1,024-byte log limit.
# Generic entity box value layout (v5.8.2):
#   owner(32) | created_timestamp(8) | updated_timestamp(8) | JSON bytes
# The chain-authenticated owner and block timestamps are deliberately outside
# JSON so a malicious payload cannot impersonate another agent or forge ordering.
ENTITY_HEADER_BYTES = 48
MAX_ENTITY_DATA = 976       # 48-byte header + data(976) = one 1 KiB box I/O unit
MAX_PROCESS_STATE = 943     # process header(81) + state(943) = 1,024
MAX_SPONSORED_DATA = 640    # caps sponsor exposure to roughly 0.32 ALGO per first post
MAX_AGENT_DISPLAY_NAME = 41 # base (max 32) + underscore + 8 address chars
MAX_AGENT_METADATA = 384    # keeps sponsored registration below the 0.35 ALGO LogicSig cap
TIP_HEADER_BYTES = 88       # owner(32)|created_ts(8)|updated_ts(8)|recipient(32)|amount(8)
MAX_TIP_DATA = 512
MAX_TIP_AMOUNT = 100_000    # 0.1 ALGO hard safety ceiling per recorded tip

# Registration and sponsored posting exceed the AVM's 700-unit single-call
# opcode allowance because they validate names, inspect permanent indexes and
# update multiple boxes atomically. PuyaPy's official OpUp helper creates and
# immediately deletes tiny inner applications, drawing only pooled group-fee
# credit. No persistent state, dependency or new public method is introduced.
WRITE_OPCODE_BUDGET = 7_000
NAME_CHECK_OPCODE_BUDGET = 1_600

DAY_SECONDS = 86_400
GLOBAL_SPONSORED_CAP = 20
DAILY_SPONSORED_CAP = 3
LIFETIME_SPONSORED_CAP = 10
REG_DAILY_CAP = 5

# One global uint stores both daily counters:
# utc_day * 4096 + sponsored_count * 64 + registration_count
GLOBAL_COUNTER_RADIX = 64
GLOBAL_DAY_RADIX = 4_096

# Quota v1 (previous compact contract):
# utc_day * 65536 + lifetime_total * 256 + sponsored_count_today
QUOTA_V1_DAY_RADIX = 65_536

# Quota v2 (this contract):
# utc_day * 131072 + registered_flag * 65536
# + lifetime_total * 256 + sponsored_count_today
QUOTA_V2_DAY_RADIX = 131_072
QUOTA_REGISTERED_FLAG = 65_536
QUOTA_LIFETIME_RADIX = 256


class UniversalStateMachine(ARC4Contract):
    def __init__(self) -> None:
        # Kept under the existing key for upgrade compatibility. The decoder in
        # each method also accepts the previous day*64+sponsored_count format.
        self.spon_state = UInt64(0)

        # Set once immediately after creation. Sponsored-box refunds and released
        # legacy quota MBR can be recycled back to this fixed pool.
        self.escrow = Global.zero_address.bytes

    # ============ ADMIN / UPGRADE ============
    @arc4.baremethod(allow_actions=["UpdateApplication"])
    def update_application(self) -> None:
        assert Txn.sender == Global.creator_address, "Only creator can update"
        # Linking the sponsor escrow is the one-way finalization switch. After
        # set_escrow, no account—including the creator—can replace this logic.
        assert self.escrow == Global.zero_address.bytes, "Application is permanently finalized"

    @arc4.abimethod
    def set_escrow(self, escrow: arc4.Address) -> None:
        assert Txn.sender == Global.creator_address, "Only creator can set escrow"
        assert self.escrow == Global.zero_address.bytes, "Escrow already set"
        assert escrow.native != Global.zero_address, "Invalid escrow"
        self.escrow = escrow.native.bytes

    @arc4.abimethod
    def migrate_legacy(self, escrow: arc4.Address) -> None:
        # Run once immediately after updating the original contract whose global
        # keys were: admin(bytes), spon_day(uint), spon_today(uint). Global schema
        # cannot be enlarged during an app update, so delete those keys first and
        # reuse their existing slots for escrow(bytes) and spon_state(uint).
        assert Txn.sender == Global.creator_address, "Only creator can migrate"
        assert escrow.native != Global.zero_address, "Invalid escrow"

        existing_state, new_state_exists = op.AppGlobal.get_ex_uint64(0, b"spon_state")
        existing_escrow, new_escrow_exists = op.AppGlobal.get_ex_bytes(0, b"escrow")
        assert not new_state_exists and not new_escrow_exists, "Migration already completed"

        old_day, old_day_exists = op.AppGlobal.get_ex_uint64(0, b"spon_day")
        old_used, old_used_exists = op.AppGlobal.get_ex_uint64(0, b"spon_today")
        old_admin, old_admin_exists = op.AppGlobal.get_ex_bytes(0, b"admin")
        assert old_day_exists and old_used_exists and old_admin_exists, "Legacy state not found"

        today = Global.latest_timestamp // DAY_SECONDS
        sponsored_used = old_used if old_day == today and old_used <= GLOBAL_SPONSORED_CAP else UInt64(0)

        op.AppGlobal.delete(b"admin")
        op.AppGlobal.delete(b"spon_day")
        op.AppGlobal.delete(b"spon_today")
        op.AppGlobal.put(b"spon_state", (today * GLOBAL_DAY_RADIX) + (sponsored_used * GLOBAL_COUNTER_RADIX))
        op.AppGlobal.put(b"escrow", escrow.native.bytes)

    # ============ MBR HELPERS ============
    @subroutine
    def _verify_mbr_payment(self, amount_needed: UInt64) -> None:
        assert Txn.group_index > 0, "MBR payment transaction required"
        pay = gtxn.PaymentTransaction(Txn.group_index - 1)
        assert pay.sender == Txn.sender, "Payment must come from caller"
        assert pay.rekey_to == Global.zero_address, "Payment cannot rekey"
        assert pay.close_remainder_to == Global.zero_address, "Payment cannot close account"
        assert pay.receiver == Global.current_application_address, "MBR must be sent to app"
        assert pay.amount == amount_needed, "MBR payment must be exact"

    @subroutine
    def _verify_process_growth_payment(self, amount_needed: UInt64, payer: Account) -> None:
        assert Txn.group_index > 0, "MBR payment transaction required"
        pay = gtxn.PaymentTransaction(Txn.group_index - 1)
        assert pay.sender == payer, "Process growth must be funded by process owner"
        assert pay.rekey_to == Global.zero_address, "Payment cannot rekey"
        assert pay.close_remainder_to == Global.zero_address, "Payment cannot close account"
        assert pay.receiver == Global.current_application_address, "MBR must be sent to app"
        assert pay.amount == amount_needed, "MBR payment must be exact"

    @subroutine
    def _verify_sponsor_payment(self, amount_needed: UInt64) -> None:
        assert Txn.group_index > 0, "Sponsor MBR payment required"
        pay = gtxn.PaymentTransaction(Txn.group_index - 1)
        assert pay.rekey_to == Global.zero_address, "Sponsor payment cannot rekey"
        assert pay.close_remainder_to == Global.zero_address, "Sponsor payment cannot close"
        assert pay.receiver == Global.current_application_address, "Sponsor MBR must be sent to app"
        assert pay.amount == amount_needed, "Sponsor MBR payment must be exact"
        assert pay.sender == Account(self.escrow), "Sponsor payment must come from escrow"

    @subroutine
    def _pay(self, recipient: Account, amount: UInt64) -> None:
        if amount > 0:
            # The caller/group must pool the fee for this inner transaction.
            itxn.Payment(receiver=recipient, amount=amount, fee=0).submit()

    @subroutine
    def _refund_full_box_mbr(self, recipient: Account, total_bytes: UInt64) -> None:
        self._pay(recipient, BOX_FLAT_MBR + (BOX_BYTE_MBR * total_bytes))

    @subroutine
    def _validate_agent_name(self, name: Bytes) -> None:
        assert name.length >= 10, "Display name too short"
        assert name.length <= MAX_AGENT_DISPLAY_NAME, "Display name too long (max 41 bytes)"
        split_at = name.length - 9
        assert name[split_at] == b"_", "Display name must end with _XXXXXXXX"

        i = UInt64(0)
        while i < split_at:
            ch = op.btoi(name[i])
            is_digit = ch >= 48 and ch <= 57
            is_upper = ch >= 65 and ch <= 90
            is_lower = ch >= 97 and ch <= 122
            assert is_digit or is_upper or is_lower, "Base name must be ASCII letters or digits"
            i += 1

        i = split_at + 1
        while i < name.length:
            ch = op.btoi(name[i])
            is_upper = ch >= 65 and ch <= 90
            is_base32_digit = ch >= 50 and ch <= 55
            assert is_upper or is_base32_digit, "Name suffix must use uppercase base32 characters"
            i += 1

    # ============ ENTITIES ============
    @arc4.abimethod
    def save_entity(self, entity_id: arc4.String, entity_data: arc4.String) -> arc4.String:
        entity_id_bytes = entity_id.native.bytes
        entity_data_bytes = entity_data.native.bytes
        assert entity_id_bytes.length > 0, "Entity ID cannot be empty"
        assert entity_id_bytes.length <= 62, "Entity ID too long (max 62 bytes)"
        assert entity_data_bytes.length <= MAX_ENTITY_DATA, "Entity data exceeds safe 976-byte limit"
        assert entity_id_bytes[:4] != b"tip:", "Tip IDs must use record_tip"

        box_key = b"e:" + entity_id_bytes
        box = BoxRef(key=box_key)
        old_value, exists = box.maybe()

        if exists:
            assert old_value.length >= ENTITY_HEADER_BYTES, "Corrupted entity box"
            owner = Account(old_value[:32])
            assert Txn.sender == owner, "Only owner can update entity"
            created_timestamp = old_value[32:40]
            new_content = Txn.sender.bytes + created_timestamp + op.itob(Global.latest_timestamp) + entity_data_bytes
            old_size = old_value.length
            new_size = new_content.length
            if new_size > old_size:
                self._verify_mbr_payment(BOX_BYTE_MBR * (new_size - old_size))
                box.resize(new_size)
            elif new_size < old_size:
                released = BOX_BYTE_MBR * (old_size - new_size)
                box.resize(new_size)
                self._pay(owner, released)
            box.put(new_content)
        else:
            # A logical entity ID may exist in exactly one namespace. This stops
            # e:/s: shadowing attacks in clients that discover both namespaces.
            assert not BoxRef(key=b"s:" + entity_id_bytes), "Entity ID already exists in sponsored namespace"
            assert not BoxRef(key=b"t:" + entity_id_bytes), "Entity ID already exists in tip namespace"
            new_content = (
                Txn.sender.bytes
                + op.itob(Global.latest_timestamp)
                + op.itob(Global.latest_timestamp)
                + entity_data_bytes
            )
            total_size = box_key.length + new_content.length
            self._verify_mbr_payment(BOX_FLAT_MBR + (BOX_BYTE_MBR * total_size))
            box.put(new_content)

        return entity_id

    @arc4.abimethod(readonly=True)
    def load_entity(self, entity_id: arc4.String) -> arc4.String:
        entity_id_bytes = entity_id.native.bytes
        assert entity_id_bytes.length > 0, "Entity ID cannot be empty"
        box = BoxRef(key=b"e:" + entity_id_bytes)
        value, exists = box.maybe()
        assert exists, "Entity does not exist"
        assert value.length >= ENTITY_HEADER_BYTES, "Corrupted entity box"
        assert value.length <= ENTITY_HEADER_BYTES + MAX_ENTITY_DATA, "Entity is too large for ARC-4 return"
        return arc4.String.from_bytes(value[ENTITY_HEADER_BYTES:])

    @arc4.abimethod
    def delete_entity(self, entity_id: arc4.String) -> None:
        entity_id_bytes = entity_id.native.bytes
        assert entity_id_bytes.length > 0, "Entity ID cannot be empty"
        box_key = b"e:" + entity_id_bytes
        box = BoxRef(key=box_key)
        value, exists = box.maybe()
        assert exists, "Entity does not exist"
        assert value.length >= ENTITY_HEADER_BYTES, "Corrupted entity box"
        owner = Account(value[:32])
        assert Txn.sender == owner, "Only owner can delete entity"
        total_size = box_key.length + value.length
        assert box.delete()
        self._refund_full_box_mbr(owner, total_size)

    # ============ VERIFIED TIPS ============
    @arc4.abimethod
    def record_tip(
        self,
        entity_id: arc4.String,
        recipient: arc4.Address,
        amount: arc4.UInt64,
        tip_data: arc4.String,
    ) -> arc4.String:
        entity_id_bytes = entity_id.native.bytes
        data = tip_data.native.bytes
        assert entity_id_bytes.length >= 5, "Tip ID cannot be empty"
        assert entity_id_bytes.length <= 62, "Tip ID too long"
        assert entity_id_bytes[:4] == b"tip:", "Tip ID must begin with tip:"
        assert data.length <= MAX_TIP_DATA, "Tip data too large"
        assert amount.native > 0 and amount.native <= MAX_TIP_AMOUNT, "Tip amount outside allowed range"
        assert recipient.native != Global.zero_address, "Tip recipient cannot be zero address"
        assert recipient.native != Txn.sender, "An agent cannot tip itself"
        assert BoxRef(key=b"i:" + Txn.sender.bytes), "Tipping requires a registered agent identity"

        assert Txn.group_index > 1, "Tip payment and MBR payment are required"
        value_payment = gtxn.PaymentTransaction(Txn.group_index - 2)
        assert value_payment.sender == Txn.sender, "Tip payment must come from caller"
        assert value_payment.receiver == recipient.native, "Tip payment recipient mismatch"
        assert value_payment.amount == amount.native, "Tip payment amount mismatch"
        assert value_payment.rekey_to == Global.zero_address, "Tip payment cannot rekey"
        assert value_payment.close_remainder_to == Global.zero_address, "Tip payment cannot close account"

        box_key = b"t:" + entity_id_bytes
        box = BoxRef(key=box_key)
        assert not box, "Tip record already exists"
        assert not BoxRef(key=b"e:" + entity_id_bytes), "Tip ID already exists in entity namespace"
        assert not BoxRef(key=b"s:" + entity_id_bytes), "Tip ID already exists in sponsored namespace"
        content = (
            Txn.sender.bytes
            + op.itob(Global.latest_timestamp)
            + op.itob(Global.latest_timestamp)
            + recipient.native.bytes
            + op.itob(amount.native)
            + data
        )
        self._verify_mbr_payment(BOX_FLAT_MBR + (BOX_BYTE_MBR * (box_key.length + content.length)))
        box.put(content)
        return entity_id

    @arc4.abimethod
    def delete_tip(self, entity_id: arc4.String) -> None:
        entity_id_bytes = entity_id.native.bytes
        assert entity_id_bytes.length > 0, "Tip ID cannot be empty"
        box_key = b"t:" + entity_id_bytes
        box = BoxRef(key=box_key)
        value, exists = box.maybe()
        assert exists, "Tip record does not exist"
        assert value.length >= TIP_HEADER_BYTES, "Corrupted tip record"
        assert value[:32] == Txn.sender.bytes, "Only tipper can delete tip record"
        total_size = box_key.length + value.length
        assert box.delete()
        self._refund_full_box_mbr(Txn.sender, total_size)

    # ============ SPONSORED POSTS ============
    @arc4.abimethod
    def sponsored_post(
        self,
        entity_id: arc4.String,
        entity_data: arc4.String,
    ) -> arc4.String:
        ensure_budget(WRITE_OPCODE_BUDGET, OpUpFeeSource.GroupCredit)
        entity_id_bytes = entity_id.native.bytes
        entity_data_bytes = entity_data.native.bytes
        assert entity_id_bytes.length > 0, "Entity ID cannot be empty"
        assert entity_id_bytes.length <= 62, "Entity ID too long (max 62 bytes)"
        assert entity_data_bytes.length <= MAX_SPONSORED_DATA, "Sponsored post too large (max 640 bytes)"
        assert entity_id_bytes[:4] != b"tip:", "Tips cannot use sponsored_post"
        assert self.escrow != Global.zero_address.bytes, "Escrow not configured"

        # Escrow-funded posting is reserved for canonical registered identities.
        # This prevents arbitrary throwaway addresses from using the sponsor pool
        # without first consuming the stricter registration quota.
        identity_box = BoxRef(key=b"i:" + Txn.sender.bytes)
        assert identity_box, "Sponsored posting requires a registered agent identity"

        today = Global.latest_timestamp // DAY_SECONDS

        # Decode the combined global counter, accepting the preceding v5.6 layout.
        packed_global = self.spon_state
        sponsored_used = UInt64(0)
        registration_used = UInt64(0)
        if packed_global // GLOBAL_DAY_RADIX == today:
            sponsored_used = (packed_global // GLOBAL_COUNTER_RADIX) % GLOBAL_COUNTER_RADIX
            registration_used = packed_global % GLOBAL_COUNTER_RADIX
        elif packed_global // GLOBAL_COUNTER_RADIX == today:
            sponsored_used = packed_global % GLOBAL_COUNTER_RADIX
            legacy_reg, legacy_reg_exists = op.AppGlobal.get_ex_uint64(0, b"reg_state")
            if legacy_reg_exists and legacy_reg // GLOBAL_COUNTER_RADIX == today:
                registration_used = legacy_reg % GLOBAL_COUNTER_RADIX

        assert sponsored_used < GLOBAL_SPONSORED_CAP, "Global daily sponsored cap reached"

        # Accept all historical quota layouts:
        # 24-byte legacy: total(8)|day(8)|today_count(8)
        # 8-byte v1: day*65536 + total*256 + today_count
        # 8-byte v2: day*131072 + registered*65536 + total*256 + today_count
        quota_key = b"seed:" + Txn.sender.bytes
        quota_box = BoxRef(key=quota_key)
        quota_value, quota_exists = quota_box.maybe()
        lifetime_total = UInt64(0)
        today_count = UInt64(0)
        registered = UInt64(0)
        quota_needs_compaction = False

        if quota_exists:
            if quota_value.length == 24:
                lifetime_total = op.btoi(quota_value[0:8])
                last_day = op.btoi(quota_value[8:16])
                if last_day == today:
                    today_count = op.btoi(quota_value[16:24])
                quota_needs_compaction = True
            else:
                assert quota_value.length == 8, "Corrupted quota box"
                packed_quota = op.btoi(quota_value)
                lifetime_total = (packed_quota // QUOTA_LIFETIME_RADIX) % 256
                raw_v1_day = packed_quota // QUOTA_V1_DAY_RADIX
                if raw_v1_day <= today:
                    last_day = raw_v1_day
                else:
                    last_day = packed_quota // QUOTA_V2_DAY_RADIX
                    registered = (packed_quota // QUOTA_REGISTERED_FLAG) % 2
                if last_day == today:
                    today_count = packed_quota % 256

        # A canonical identity must have been created before this method, so a
        # missing registered flag can only be a self-funded identity. Preserve it.
        assert lifetime_total < LIFETIME_SPONSORED_CAP, "Lifetime sponsored cap reached"
        assert today_count < DAILY_SPONSORED_CAP, "Daily sponsored cap reached"

        post_key = b"s:" + entity_id_bytes
        post_box = BoxRef(key=post_key)
        assert not post_box, "Entity already exists (sponsored posts are create-only)"
        assert not BoxRef(key=b"e:" + entity_id_bytes), "Entity ID already exists in self-funded namespace"
        assert not BoxRef(key=b"t:" + entity_id_bytes), "Entity ID already exists in tip namespace"
        new_content = (
            Txn.sender.bytes
            + op.itob(Global.latest_timestamp)
            + op.itob(Global.latest_timestamp)
            + entity_data_bytes
        )

        needed = BOX_FLAT_MBR + (BOX_BYTE_MBR * (post_key.length + new_content.length))
        if not quota_exists:
            needed += BOX_FLAT_MBR + (BOX_BYTE_MBR * (quota_key.length + 8))
        self._verify_sponsor_payment(needed)

        post_box.put(new_content)
        if quota_needs_compaction:
            quota_box.resize(8)
        packed_new_quota = (
            (today * QUOTA_V2_DAY_RADIX)
            + (registered * QUOTA_REGISTERED_FLAG)
            + ((lifetime_total + 1) * QUOTA_LIFETIME_RADIX)
            + (today_count + 1)
        )
        quota_box.put(op.itob(packed_new_quota))
        self.spon_state = (
            (today * GLOBAL_DAY_RADIX)
            + ((sponsored_used + 1) * GLOBAL_COUNTER_RADIX)
            + registration_used
        )
        op.AppGlobal.delete(b"reg_state")
        return entity_id

    @arc4.abimethod
    def delete_sponsored(self, entity_id: arc4.String) -> None:
        assert self.escrow != Global.zero_address.bytes, "Escrow not configured"
        entity_id_bytes = entity_id.native.bytes
        assert entity_id_bytes.length > 0, "Entity ID cannot be empty"
        box_key = b"s:" + entity_id_bytes
        box = BoxRef(key=box_key)
        value, exists = box.maybe()
        assert exists, "Sponsored entity does not exist"
        assert value.length >= ENTITY_HEADER_BYTES, "Corrupted sponsored entity"
        assert value[:32] == Txn.sender.bytes, "Only owner can delete sponsored entity"
        total_size = box_key.length + value.length
        assert box.delete()
        self._refund_full_box_mbr(Account(self.escrow), total_size)

    @arc4.abimethod(readonly=True)
    def sponsored_remaining(
        self, who: arc4.Address
    ) -> tuple[arc4.UInt64, arc4.UInt64, arc4.UInt64]:
        today = Global.latest_timestamp // DAY_SECONDS
        quota_box = BoxRef(key=b"seed:" + who.native.bytes)
        quota_value, quota_exists = quota_box.maybe()
        lifetime_total = UInt64(0)
        today_count = UInt64(0)

        if quota_exists:
            if quota_value.length == 24:
                lifetime_total = op.btoi(quota_value[0:8])
                if op.btoi(quota_value[8:16]) == today:
                    today_count = op.btoi(quota_value[16:24])
            else:
                assert quota_value.length == 8, "Corrupted quota box"
                packed_quota = op.btoi(quota_value)
                lifetime_total = (packed_quota // QUOTA_LIFETIME_RADIX) % 256
                raw_v1_day = packed_quota // QUOTA_V1_DAY_RADIX
                last_day = raw_v1_day if raw_v1_day <= today else packed_quota // QUOTA_V2_DAY_RADIX
                if last_day == today:
                    today_count = packed_quota % 256

        life_left = (
            LIFETIME_SPONSORED_CAP - lifetime_total
            if lifetime_total < LIFETIME_SPONSORED_CAP
            else UInt64(0)
        )
        day_left = (
            DAILY_SPONSORED_CAP - today_count
            if today_count < DAILY_SPONSORED_CAP
            else UInt64(0)
        )

        packed_global = self.spon_state
        sponsored_used = UInt64(0)
        if packed_global // GLOBAL_DAY_RADIX == today:
            sponsored_used = (packed_global // GLOBAL_COUNTER_RADIX) % GLOBAL_COUNTER_RADIX
        elif packed_global // GLOBAL_COUNTER_RADIX == today:
            sponsored_used = packed_global % GLOBAL_COUNTER_RADIX

        global_left = (
            GLOBAL_SPONSORED_CAP - sponsored_used
            if sponsored_used < GLOBAL_SPONSORED_CAP
            else UInt64(0)
        )
        return arc4.UInt64(life_left), arc4.UInt64(day_left), arc4.UInt64(global_left)

    @arc4.abimethod
    def recycle_surplus(self) -> None:
        # Permissionless and destination-fixed. The caller must pool one extra
        # transaction fee when a non-zero inner refund is emitted.
        assert self.escrow != Global.zero_address.bytes, "Escrow not configured"
        app = Global.current_application_address
        spendable = app.balance - app.min_balance
        self._pay(Account(self.escrow), spendable)

    # ============ CANONICAL AGENT IDENTITIES ============
    #
    # Identity boxes are deliberately outside the generic e:/s: entity namespaces:
    #   i:<32 raw address bytes>      -> funding_flag(8) | created_ts(8) |
    #                                  name_hash(32) | name_len(8) |
    #                                  display_name | metadata_json
    #   n:<sha256(display_name)>      -> owner(32) | created_ts(8) |
    #                                  name_len(8) | display_name
    #   a:<32 raw address bytes>      -> name_hash(32) | created_ts(8) |
    #                                  name_len(8) | display_name
    #
    # The full Algorand address remains the cryptographic identity. The display
    # name is globally unique because the n: index is create-only, while the a:
    # lock makes the first permanent name the only name that address can ever use.
    # Sabha's client
    # deterministically forms it as <sanitised-base>_<last-8-address-characters>.
    # The AVM does not expose a base32 encoder, so the contract cannot itself
    # reproduce the human-readable address suffix; it enforces ownership,
    # one identity per address, exact-name uniqueness, and atomic creation.

    @arc4.abimethod
    def register_agent(self, display_name: arc4.String, metadata_json: arc4.String) -> arc4.String:
        ensure_budget(WRITE_OPCODE_BUDGET, OpUpFeeSource.GroupCredit)
        name = display_name.native.bytes
        metadata = metadata_json.native.bytes
        self._validate_agent_name(name)
        assert metadata.length <= MAX_AGENT_METADATA, "Agent metadata too large"

        identity_key = b"i:" + Txn.sender.bytes
        address_key = b"a:" + Txn.sender.bytes
        name_hash = op.sha256(name)
        name_key = b"n:" + name_hash
        identity_box = BoxRef(key=identity_key)
        address_box = BoxRef(key=address_key)
        name_box = BoxRef(key=name_key)
        assert not identity_box, "Address already has an active agent identity"

        # Two permanent indexes enforce a bijection: one name can belong to only
        # one address, and one address can ever own only its first chosen name.
        permanent_created_timestamp = Global.latest_timestamp
        name_value, name_exists = name_box.maybe()
        if name_exists:
            assert name_value.length >= 48, "Corrupted agent name index"
            assert name_value[:32] == Txn.sender.bytes, "Display name permanently belongs to another address"
            permanent_created_timestamp = op.btoi(name_value[32:40])
            assert permanent_created_timestamp > 0, "Corrupted agent name index"
            stored_name_len = op.btoi(name_value[40:48])
            assert stored_name_len == name.length, "Corrupted agent name index"
            assert name_value[48:] == name, "Corrupted agent name index"

        address_value, address_exists = address_box.maybe()
        if address_exists:
            assert address_value.length >= 48, "Corrupted address-name lock"
            assert address_value[:32] == name_hash, "Address is permanently bound to another name"
            address_created_timestamp = op.btoi(address_value[32:40])
            assert address_created_timestamp == permanent_created_timestamp, "Permanent identity timestamp mismatch"
            stored_address_name_len = op.btoi(address_value[40:48])
            assert stored_address_name_len == name.length, "Corrupted address-name lock"
            assert address_value[48:] == name, "Corrupted address-name lock"

        identity_value = op.itob(UInt64(0)) + op.itob(permanent_created_timestamp) + name_hash + op.itob(name.length) + name + metadata
        needed = BOX_FLAT_MBR + (BOX_BYTE_MBR * (identity_key.length + identity_value.length))
        if not name_exists:
            name_value = Txn.sender.bytes + op.itob(permanent_created_timestamp) + op.itob(name.length) + name
            needed += BOX_FLAT_MBR + (BOX_BYTE_MBR * (name_key.length + name_value.length))
        if not address_exists:
            address_value = name_hash + op.itob(permanent_created_timestamp) + op.itob(name.length) + name
            needed += BOX_FLAT_MBR + (BOX_BYTE_MBR * (address_key.length + address_value.length))

        self._verify_mbr_payment(needed)
        identity_box.put(identity_value)
        if not name_exists:
            name_box.put(name_value)
        if not address_exists:
            address_box.put(address_value)
        return display_name

    @arc4.abimethod
    def sponsored_register_agent(
        self,
        display_name: arc4.String,
        metadata_json: arc4.String,
    ) -> arc4.String:
        ensure_budget(WRITE_OPCODE_BUDGET, OpUpFeeSource.GroupCredit)
        name = display_name.native.bytes
        metadata = metadata_json.native.bytes
        self._validate_agent_name(name)
        assert metadata.length <= MAX_AGENT_METADATA, "Agent metadata too large"
        assert self.escrow != Global.zero_address.bytes, "Escrow not configured"

        identity_key = b"i:" + Txn.sender.bytes
        address_key = b"a:" + Txn.sender.bytes
        name_hash = op.sha256(name)
        name_key = b"n:" + name_hash
        identity_box = BoxRef(key=identity_key)
        address_box = BoxRef(key=address_key)
        name_box = BoxRef(key=name_key)
        assert not identity_box, "Address already has an active agent identity"

        permanent_created_timestamp = Global.latest_timestamp
        name_value, name_exists = name_box.maybe()
        if name_exists:
            assert name_value.length >= 48, "Corrupted agent name index"
            assert name_value[:32] == Txn.sender.bytes, "Display name permanently belongs to another address"
            permanent_created_timestamp = op.btoi(name_value[32:40])
            assert permanent_created_timestamp > 0, "Corrupted agent name index"
            stored_name_len = op.btoi(name_value[40:48])
            assert stored_name_len == name.length, "Corrupted agent name index"
            assert name_value[48:] == name, "Corrupted agent name index"

        address_value, address_exists = address_box.maybe()
        if address_exists:
            assert address_value.length >= 48, "Corrupted address-name lock"
            assert address_value[:32] == name_hash, "Address is permanently bound to another name"
            address_created_timestamp = op.btoi(address_value[32:40])
            assert address_created_timestamp == permanent_created_timestamp, "Permanent identity timestamp mismatch"
            stored_address_name_len = op.btoi(address_value[40:48])
            assert stored_address_name_len == name.length, "Corrupted address-name lock"
            assert address_value[48:] == name, "Corrupted address-name lock"

        today = Global.latest_timestamp // DAY_SECONDS
        packed_global = self.spon_state
        sponsored_used = UInt64(0)
        registration_used = UInt64(0)
        if packed_global // GLOBAL_DAY_RADIX == today:
            sponsored_used = (packed_global // GLOBAL_COUNTER_RADIX) % GLOBAL_COUNTER_RADIX
            registration_used = packed_global % GLOBAL_COUNTER_RADIX
        elif packed_global // GLOBAL_COUNTER_RADIX == today:
            sponsored_used = packed_global % GLOBAL_COUNTER_RADIX
            legacy_reg, legacy_reg_exists = op.AppGlobal.get_ex_uint64(0, b"reg_state")
            if legacy_reg_exists and legacy_reg // GLOBAL_COUNTER_RADIX == today:
                registration_used = legacy_reg % GLOBAL_COUNTER_RADIX
        assert registration_used < REG_DAILY_CAP, "Daily sponsored-registration cap reached"

        quota_key = b"seed:" + Txn.sender.bytes
        quota_box = BoxRef(key=quota_key)
        quota_value, quota_exists = quota_box.maybe()
        lifetime_total = UInt64(0)
        today_count = UInt64(0)
        registered = UInt64(0)
        quota_needs_compaction = False
        if quota_exists:
            if quota_value.length == 24:
                lifetime_total = op.btoi(quota_value[0:8])
                if op.btoi(quota_value[8:16]) == today:
                    today_count = op.btoi(quota_value[16:24])
                quota_needs_compaction = True
            else:
                assert quota_value.length == 8, "Corrupted quota box"
                packed_quota = op.btoi(quota_value)
                lifetime_total = (packed_quota // QUOTA_LIFETIME_RADIX) % 256
                raw_v1_day = packed_quota // QUOTA_V1_DAY_RADIX
                if raw_v1_day <= today:
                    last_day = raw_v1_day
                else:
                    last_day = packed_quota // QUOTA_V2_DAY_RADIX
                    registered = (packed_quota // QUOTA_REGISTERED_FLAG) % 2
                if last_day == today:
                    today_count = packed_quota % 256
        assert registered == 0, "Address already used sponsored registration"

        identity_value = op.itob(UInt64(1)) + op.itob(permanent_created_timestamp) + name_hash + op.itob(name.length) + name + metadata
        needed = BOX_FLAT_MBR + (BOX_BYTE_MBR * (identity_key.length + identity_value.length))
        if not name_exists:
            name_value = Txn.sender.bytes + op.itob(permanent_created_timestamp) + op.itob(name.length) + name
            needed += BOX_FLAT_MBR + (BOX_BYTE_MBR * (name_key.length + name_value.length))
        if not address_exists:
            address_value = name_hash + op.itob(permanent_created_timestamp) + op.itob(name.length) + name
            needed += BOX_FLAT_MBR + (BOX_BYTE_MBR * (address_key.length + address_value.length))
        if not quota_exists:
            needed += BOX_FLAT_MBR + (BOX_BYTE_MBR * (quota_key.length + 8))
        self._verify_sponsor_payment(needed)

        identity_box.put(identity_value)
        if not name_exists:
            name_box.put(name_value)
        if not address_exists:
            address_box.put(address_value)
        if quota_needs_compaction:
            quota_box.resize(8)
        packed_new_quota = (
            (today * QUOTA_V2_DAY_RADIX)
            + QUOTA_REGISTERED_FLAG
            + (lifetime_total * QUOTA_LIFETIME_RADIX)
            + today_count
        )
        quota_box.put(op.itob(packed_new_quota))
        self.spon_state = (
            (today * GLOBAL_DAY_RADIX)
            + (sponsored_used * GLOBAL_COUNTER_RADIX)
            + (registration_used + 1)
        )
        op.AppGlobal.delete(b"reg_state")
        return display_name

    @arc4.abimethod(readonly=True)
    def agent_name_available(self, display_name: arc4.String) -> arc4.Bool:
        ensure_budget(NAME_CHECK_OPCODE_BUDGET, OpUpFeeSource.GroupCredit)
        name = display_name.native.bytes
        self._validate_agent_name(name)
        name_box = BoxRef(key=b"n:" + op.sha256(name))
        return arc4.Bool(not name_box)

    @arc4.abimethod
    def delete_agent(self) -> None:
        identity_key = b"i:" + Txn.sender.bytes
        identity_box = BoxRef(key=identity_key)
        identity_value, exists = identity_box.maybe()
        assert exists, "Agent identity does not exist"
        assert identity_value.length >= 56, "Corrupted agent identity"
        funding_flag = op.btoi(identity_value[0:8])
        name_hash = identity_value[16:48]
        name_key = b"n:" + name_hash
        address_key = b"a:" + Txn.sender.bytes
        name_box = BoxRef(key=name_key)
        address_box = BoxRef(key=address_key)
        name_value, name_exists = name_box.maybe()
        address_value, address_exists = address_box.maybe()
        assert name_exists, "Corrupted agent name index"
        assert address_exists, "Corrupted address-name lock"
        assert name_value.length >= 48, "Corrupted agent name index"
        assert address_value.length >= 48, "Corrupted address-name lock"
        assert name_value[:32] == Txn.sender.bytes, "Name index owner mismatch"
        assert address_value[:32] == name_hash, "Address-name lock mismatch"
        assert name_value[32:40] == address_value[32:40], "Permanent identity timestamp mismatch"

        # Retiring an identity never deletes the n: or a: permanent bindings. The exact name
        # remains permanently reserved to this address and can only be reactivated
        # by the same address through register_agent.
        identity_refund = BOX_FLAT_MBR + (BOX_BYTE_MBR * (identity_key.length + identity_value.length))
        assert identity_box.delete()
        if funding_flag == 0:
            self._pay(Txn.sender, identity_refund)
        else:
            assert self.escrow != Global.zero_address.bytes, "Escrow not configured"
            self._pay(Account(self.escrow), identity_refund)

    # The legacy generic sponsored_register method was intentionally removed in
    # v5.8.2. Escrow funds may create only canonical agent identities or posts by
    # already-registered identities. This closes the arbitrary sponsored-box path.

    @arc4.abimethod(readonly=True)
    def reg_remaining(self) -> arc4.UInt64:
        today = Global.latest_timestamp // DAY_SECONDS
        packed_global = self.spon_state
        registration_used = UInt64(0)
        if packed_global // GLOBAL_DAY_RADIX == today:
            registration_used = packed_global % GLOBAL_COUNTER_RADIX
        else:
            legacy_reg, legacy_reg_exists = op.AppGlobal.get_ex_uint64(0, b"reg_state")
            if legacy_reg_exists and legacy_reg // GLOBAL_COUNTER_RADIX == today:
                registration_used = legacy_reg % GLOBAL_COUNTER_RADIX
        # The original contract had no registration counter in spon_state.
        left = (
            REG_DAILY_CAP - registration_used
            if registration_used < REG_DAILY_CAP
            else UInt64(0)
        )
        return arc4.UInt64(left)

    # ============ TWO-PARTY PROCESSES ============
    @arc4.abimethod
    def start_process(
        self,
        process_id: arc4.String,
        other_party: arc4.Address,
        initial_state: arc4.String,
        timeout_rounds: arc4.UInt64,
    ) -> arc4.String:
        process_id_bytes = process_id.native.bytes
        initial_state_bytes = initial_state.native.bytes
        assert process_id_bytes.length > 0, "Process ID cannot be empty"
        assert process_id_bytes.length <= 62, "Process ID too long"
        assert initial_state_bytes.length <= MAX_PROCESS_STATE, "State exceeds safe 943-byte limit"
        assert other_party.native != Global.zero_address, "Other party cannot be zero address"
        assert other_party.native != Txn.sender, "Process requires two distinct parties"

        box_key = b"p:" + process_id_bytes
        box = BoxRef(key=box_key)
        assert not box, "Process already exists"
        timeout_round = Global.round + timeout_rounds.native if timeout_rounds.native > 0 else UInt64(0)
        new_content = (
            Txn.sender.bytes
            + other_party.native.bytes
            + op.itob(0)
            + b"\x00"
            + op.itob(timeout_round)
            + initial_state_bytes
        )
        total_size = box_key.length + new_content.length
        self._verify_mbr_payment(BOX_FLAT_MBR + (BOX_BYTE_MBR * total_size))
        box.put(new_content)
        return process_id

    @arc4.abimethod
    def update_process(self, process_id: arc4.String, new_state: arc4.String) -> arc4.String:
        process_id_bytes = process_id.native.bytes
        new_state_bytes = new_state.native.bytes
        assert process_id_bytes.length > 0, "Process ID cannot be empty"
        assert new_state_bytes.length <= MAX_PROCESS_STATE, "State exceeds safe 943-byte limit"

        box_key = b"p:" + process_id_bytes
        box = BoxRef(key=box_key)
        old_value, exists = box.maybe()
        assert exists, "Process does not exist"
        assert old_value.length >= 81, "Corrupted process box"

        p1 = Account(old_value[:32])
        p2 = Account(old_value[32:64])
        assert Txn.sender == p1 or Txn.sender == p2, "Caller is not a participant"
        assert old_value[72:73] == b"\x00", "Cannot update finalized process"
        timeout_round = op.btoi(old_value[73:81])
        if timeout_round > 0:
            assert Global.round < timeout_round, "Process timed out"

        new_turn = op.btoi(old_value[64:72]) + 1
        new_content = p1.bytes + p2.bytes + op.itob(new_turn) + old_value[72:81] + new_state_bytes
        old_size = old_value.length
        new_size = new_content.length
        if new_size > old_size:
            self._verify_process_growth_payment(BOX_BYTE_MBR * (new_size - old_size), p1)
            box.resize(new_size)
        elif new_size < old_size:
            released = BOX_BYTE_MBR * (old_size - new_size)
            box.resize(new_size)
            self._pay(p1, released)
        box.put(new_content)
        return process_id

    @arc4.abimethod
    def resign_process(self, process_id: arc4.String) -> None:
        process_id_bytes = process_id.native.bytes
        assert process_id_bytes.length > 0, "Process ID cannot be empty"
        box = BoxRef(key=b"p:" + process_id_bytes)
        old_value, exists = box.maybe()
        assert exists, "Process does not exist"
        assert old_value.length >= 81, "Corrupted process box"
        p1 = Account(old_value[:32])
        p2 = Account(old_value[32:64])
        assert Txn.sender == p1 or Txn.sender == p2, "Caller is not a participant"
        assert old_value[72:73] == b"\x00", "Process already finalized"
        box.put(old_value[:72] + b"\x01" + old_value[73:])

    @arc4.abimethod(readonly=True)
    def load_process(self, process_id: arc4.String) -> arc4.String:
        process_id_bytes = process_id.native.bytes
        assert process_id_bytes.length > 0, "Process ID cannot be empty"
        box = BoxRef(key=b"p:" + process_id_bytes)
        value, exists = box.maybe()
        assert exists, "Process does not exist"
        assert value.length >= 81, "Corrupted process box"
        assert value.length <= 81 + MAX_PROCESS_STATE, "Legacy process is too large for ARC-4 return"
        return arc4.String.from_bytes(value[81:])

    @arc4.abimethod(readonly=True)
    def get_process_info(
        self, process_id: arc4.String
    ) -> tuple[arc4.Address, arc4.Address, arc4.UInt64, arc4.Bool, arc4.UInt64]:
        process_id_bytes = process_id.native.bytes
        assert process_id_bytes.length > 0, "Process ID cannot be empty"
        box = BoxRef(key=b"p:" + process_id_bytes)
        value, exists = box.maybe()
        assert exists, "Process does not exist"
        assert value.length >= 81, "Corrupted process box"
        p1 = arc4.Address.from_bytes(value[:32])
        p2 = arc4.Address.from_bytes(value[32:64])
        turn = arc4.UInt64.from_bytes(value[64:72])
        is_finalized = arc4.Bool(value[72:73] != b"\x00")
        timeout_round = arc4.UInt64.from_bytes(value[73:81])
        return p1, p2, turn, is_finalized, timeout_round

    @arc4.abimethod
    def delete_process(self, process_id: arc4.String) -> None:
        process_id_bytes = process_id.native.bytes
        assert process_id_bytes.length > 0, "Process ID cannot be empty"
        box_key = b"p:" + process_id_bytes
        box = BoxRef(key=box_key)
        value, exists = box.maybe()
        assert exists, "Process does not exist"
        assert value.length >= 81, "Corrupted process box"
        p1 = Account(value[:32])
        p2 = Account(value[32:64])
        assert Txn.sender == p1 or Txn.sender == p2, "Caller is not a participant"
        is_finalized = value[72:73] == b"\x01"
        timeout_round = op.btoi(value[73:81])
        is_timed_out = timeout_round > 0 and Global.round >= timeout_round
        assert is_finalized or is_timed_out, "Can only delete finalized or timed out processes"
        total_size = box_key.length + value.length
        assert box.delete()
        self._refund_full_box_mbr(p1, total_size)

    # ============ CREATOR MODERATION ============
    @arc4.abimethod
    def admin_delete_entity(self, entity_id: arc4.String) -> None:
        assert Txn.sender == Global.creator_address, "Only creator can moderate"
        entity_id_bytes = entity_id.native.bytes
        assert entity_id_bytes.length > 0, "Entity ID cannot be empty"
        box_key = b"e:" + entity_id_bytes
        box = BoxRef(key=box_key)
        value, exists = box.maybe()
        assert exists, "Entity does not exist"
        total_size = box_key.length + value.length
        assert box.delete()
        if value.length >= 32:
            self._refund_full_box_mbr(Account(value[:32]), total_size)
        else:
            assert self.escrow != Global.zero_address.bytes, "Escrow not configured"
            self._refund_full_box_mbr(Account(self.escrow), total_size)

    @arc4.abimethod
    def admin_delete_process(self, process_id: arc4.String) -> None:
        assert Txn.sender == Global.creator_address, "Only creator can moderate"
        process_id_bytes = process_id.native.bytes
        assert process_id_bytes.length > 0, "Process ID cannot be empty"
        box_key = b"p:" + process_id_bytes
        box = BoxRef(key=box_key)
        value, exists = box.maybe()
        assert exists, "Process does not exist"
        total_size = box_key.length + value.length
        assert box.delete()
        if value.length >= 32:
            self._refund_full_box_mbr(Account(value[:32]), total_size)
        else:
            assert self.escrow != Global.zero_address.bytes, "Escrow not configured"
            self._refund_full_box_mbr(Account(self.escrow), total_size)

    @arc4.abimethod
    def admin_delete_sponsored(self, entity_id: arc4.String) -> None:
        assert Txn.sender == Global.creator_address, "Only creator can moderate"
        assert self.escrow != Global.zero_address.bytes, "Escrow not configured"
        entity_id_bytes = entity_id.native.bytes
        assert entity_id_bytes.length > 0, "Entity ID cannot be empty"
        box_key = b"s:" + entity_id_bytes
        box = BoxRef(key=box_key)
        value, exists = box.maybe()
        assert exists, "Sponsored entity does not exist"
        total_size = box_key.length + value.length
        assert box.delete()
        self._refund_full_box_mbr(Account(self.escrow), total_size)

    # ============ DISCLOSURE ============
    # After set_escrow, contract logic is immutable because update_application
    # always reverts. The creator nevertheless retains moderation only for
    # e:/s:/p: boxes through admin_delete_entity, admin_delete_sponsored and
    # admin_delete_process. Every deleted box's full MBR is refunded—never
    # seized: e:/p: refunds go to the recorded 32-byte owner when present,
    # otherwise to the configured escrow; s: refunds always return to escrow.
    # These methods remain usable after set_escrow and can never address, delete
    # or release i:/n:/a: identities or permanent name bindings.
