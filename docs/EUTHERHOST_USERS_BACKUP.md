# EutherHost user backup and two-machine recovery

This is the canonical operational procedure for protecting EutherOxide's host
user database across the two LAN machines:

- `192.168.32.186` (`EutherServer`) creates encrypted backups.
- `192.168.32.88` (`apansson`) mirrors the encrypted files and holds the SSH
  private key needed for recovery.

The implementation lives in EutherOxide because that repository owns
`.euther-host/users.toml` and the related services. EutherNet documents the
topology, security boundary, verification, and recovery procedure.

## Protected state

The live source on `.186` is:

```text
/home/nichlas/EutherOxide/.euther-host/users.toml
```

It contains password hashes and permissions and must remain owned by
`nichlas:nichlas` with mode `0600`. It is never copied in plaintext and must
never be committed to Git.

## Backup flow

```text
users.toml on .186
  -> TOML validation
  -> age encryption with the euther_server SSH public key
  -> checksum and atomic write under /srv/backups/eutheroxide on .186
  -> read-only restricted rsync pull
  -> /home/nichlas/Backups/EutherOxide on .88
```

Server-side components from EutherOxide:

- `scripts/eutherhost-users-backup.sh`
- `deploy/eutherhost-users-backup.service`
- `deploy/eutherhost-users-backup.timer`

Workstation-side components from EutherOxide:

- `scripts/eutherhost-users-mirror.sh`
- `deploy/eutherhost-users-mirror.service`
- `deploy/eutherhost-users-mirror.timer`

The server timer starts daily at `03:55` with up to 15 minutes of random delay
and keeps 30 days. The workstation mirror starts at `04:30` with up to 15
minutes of random delay. It uses `--ignore-existing` and does not delete older
local recovery points.

Health timers run every six hours on both machines. They validate timer state,
freshness, age headers, checksum pairs, and every ciphertext checksum. A failed
check on `.88` raises a desktop notification and remains visible as a failed
user service. The `.186` result is collected by EutherNet and appears in the
server map and `GET /api/euthernet/backup-health`.

## Key boundaries

Encryption uses the public half of `.88`'s existing recovery identity:

```text
/home/nichlas/.ssh/euther_server
```

Only its public key is stored in `/etc/eutheroxide-backup/recipients` on `.186`.
The private key and its passphrase never leave `.88`.

Automated mirroring uses a separate key on `.88`:

```text
/home/nichlas/.ssh/euther_backup_pull
```

That key is intentionally dedicated to unattended copying. Its authorized-key
entry on `.186` must keep all of these restrictions:

```text
from="192.168.32.88",restrict,command="/usr/bin/rrsync -ro /srv/backups/eutheroxide"
```

The restriction permits only read-only rsync inside the encrypted backup
directory. It cannot open a shell, forward ports, write or delete server files,
or read the live `users.toml`.

The `eutherbackup` group on `.186` may read only the encrypted backup directory.
The live user database remains mode `0600` and outside that boundary.

## Routine verification

On `.186`:

```bash
systemctl is-active eutherhost-users-backup.timer
systemctl show eutherhost-users-backup.service \
  -p Result -p ExecMainStatus -p ExecMainExitTimestamp --no-pager
systemctl show eutherhost-users-backup-health.service \
  -p Result -p ExecMainStatus -p ExecMainExitTimestamp --no-pager
systemctl list-timers eutherhost-users-backup.timer --no-pager
sudo sh -c 'cd /srv/backups/eutheroxide && sha256sum -c ./*.sha256'
```

Run a server backup immediately:

```bash
sudo systemctl start eutherhost-users-backup.service
```

On `.88`:

```bash
systemctl --user is-active eutherhost-users-mirror.timer
systemctl --user show eutherhost-users-mirror.service \
  -p Result -p ExecMainStatus -p ExecMainExitTimestamp --no-pager
systemctl --user show eutherhost-users-mirror-health.service \
  -p Result -p ExecMainStatus -p ExecMainExitTimestamp --no-pager
systemctl --user list-timers eutherhost-users-mirror.timer --no-pager
find /home/nichlas/Backups/EutherOxide -maxdepth 1 -type f -printf '%m %s %f\n' | sort
```

Run a mirror immediately:

```bash
systemctl --user start eutherhost-users-mirror.service
```

Both services must finish with `Result=success` and `ExecMainStatus=0`.

## Recovery drill on .88

Choose an encrypted file and verify its ciphertext checksum before decrypting:

```bash
cd /home/nichlas/Backups/EutherOxide
backup=eutherhost-users-YYYYMMDDTHHMMSSZ.toml.age
expected="$(awk 'NR == 1 { print $1 }' "${backup}.sha256")"
actual="$(sha256sum "${backup}" | awk '{ print $1 }')"
test "${actual}" = "${expected}"
```

Decrypt to a protected temporary file. `age` prompts for the passphrase of the
existing `euther_server` SSH key:

```bash
umask 077
age --decrypt --identity /home/nichlas/.ssh/euther_server \
  --output /tmp/eutherhost-users.restore.toml "${backup}"
python3 -c 'import pathlib, tomllib; tomllib.loads(pathlib.Path("/tmp/eutherhost-users.restore.toml").read_text())'
stat -c '%a %U:%G %n' /tmp/eutherhost-users.restore.toml
```

The TOML parser must exit successfully and the temporary file must not be group
or world readable. Delete the plaintext test file as soon as the drill ends:

```bash
rm -f /tmp/eutherhost-users.restore.toml
```

## Restoring after loss on .186

1. Select and validate an encrypted recovery point on `.88` as above.
2. Decrypt it to a mode-`0600` temporary file.
3. Copy it to `.186` over the normal administrator SSH identity, not the
   restricted pull identity.
4. On `.186`, stop EutherHost before replacing the live file.
5. Preserve the damaged/current file separately, install the restored file with
   owner `nichlas:nichlas` and mode `0600`, and start EutherHost.
6. Verify service state and perform a real login with a known account.

Server-side replacement commands after the restored file has been copied to
`/tmp/eutherhost-users.restore.toml`:

```bash
sudo systemctl stop eutherhost.service
sudo install -m 0600 -o nichlas -g nichlas \
  /home/nichlas/EutherOxide/.euther-host/users.toml \
  /home/nichlas/EutherOxide/.euther-host/users.toml.pre-restore
sudo install -m 0600 -o nichlas -g nichlas \
  /tmp/eutherhost-users.restore.toml \
  /home/nichlas/EutherOxide/.euther-host/users.toml
sudo systemctl start eutherhost.service
systemctl is-active eutherhost.service
rm -f /tmp/eutherhost-users.restore.toml
```

Do not run the replacement portion as a routine test. A recovery drill ends
after local decryption and TOML validation on `.88`.

## Revoking mirror access

To revoke automated copying, disable the local mirror timer, remove the single
`euther-backup-pull@192.168.32.88` entry from `.186`'s `authorized_keys`, and
remove the dedicated local key only after confirming no other service uses it:

```bash
systemctl --user disable --now eutherhost-users-mirror.timer
```

Removing this mirror key does not affect the normal `euther_server` administrator
key or the ability to decrypt existing backups.

## Security and failure notes

- A compromise of `.186` does not expose the decryption private key.
- A compromise of the restricted mirror key exposes only already encrypted
  backup files.
- A failure of `.186` leaves the mirrored recovery points on `.88`.
- A failure of `.88` leaves the 30-day encrypted set on `.186`, but recovery
  still requires a surviving copy of the passphrase-protected `euther_server`
  private key.
- Back up that private key separately on protected offline media. Never place it
  in EutherNet, EutherOxide, or another Git repository.
