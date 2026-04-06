forwarding for the second user:

sudo usermod -aG input g

udev rule to persist after reboot:

echo 'KERNEL=="uinput", GROUP="uinput", MODE="0660"' | \ sudo tee /etc/udev/rules.d/99-uinput.rules

sudo udevadm control --reload-rules

sudo udevadm trigger /dev/uinput

after this, re-login or apply without re-login:

sudo -u g groups
