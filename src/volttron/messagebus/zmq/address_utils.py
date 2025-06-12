import hashlib
from pathlib import Path
import os

def get_vip_inproc_address(volttron_home: Path | str = None) -> str:
    """
    Get the inproc VIP address for a given VOLTTRON_HOME
    
    :param volttron_home: Path to VOLTTRON_HOME, defaults to environment variable
    :return: The inproc address string
    """
    if volttron_home is None:
        volttron_home = os.environ.get('VOLTTRON_HOME', '~/.volttron')
    
    volttron_home = Path(volttron_home).expanduser().absolute()
    volttron_home_str = str(volttron_home)
    
    # Use same logic as router
    path_hash = hashlib.md5(volttron_home_str.encode()).hexdigest()[:12]
    return f"inproc://vip-{path_hash}"

def write_vip_address_file(volttron_home: Path, inproc_address: str):
    """Write the inproc address to a discovery file"""
    address_file = volttron_home / "run" / "vip_inproc_address"
    address_file.parent.mkdir(parents=True, exist_ok=True)
    address_file.write_text(inproc_address)

def read_vip_address_file(volttron_home: Path) -> str | None:
    """Read the inproc address from discovery file"""
    address_file = volttron_home / "run" / "vip_inproc_address"
    if address_file.exists():
        return address_file.read_text().strip()
    return None