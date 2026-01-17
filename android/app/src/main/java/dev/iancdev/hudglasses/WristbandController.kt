package dev.iancdev.hudglasses

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattService
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.content.Context
import android.os.Build
import android.util.Log
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.UUID

class WristbandController(private val context: Context) {
    private val adapter: BluetoothAdapter? by lazy {
        val bm = context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
        bm.adapter
    }

    private var gatt: BluetoothGatt? = null
    private var commandChar: BluetoothGattCharacteristic? = null
    private var scanning = false
    private var scanCallback: ScanCallback? = null

    fun disconnect() {
        stopScan()
        commandChar = null
        gatt?.close()
        gatt = null
        HudStore.update { it.copy(wristbandConnected = false) }
    }

    @SuppressLint("MissingPermission")
    fun connectByScan(namePrefix: String, serviceUuid: UUID, commandCharUuid: UUID) {
        disconnect()
        val scanner = adapter?.bluetoothLeScanner ?: return
        scanning = true
        val cb = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult) {
                val device = result.device ?: return
                val name = device.name ?: return
                if (!name.startsWith(namePrefix)) return
                stopScan()
                connectGatt(device, serviceUuid, commandCharUuid)
            }

            override fun onScanFailed(errorCode: Int) {
                stopScan()
                Log.w("Wristband", "scan failed code=$errorCode")
            }
        }
        scanCallback = cb
        scanner.startScan(cb)
    }

    @SuppressLint("MissingPermission")
    private fun stopScan() {
        if (!scanning) return
        scanning = false
        val scanner = adapter?.bluetoothLeScanner ?: return
        try {
            scanCallback?.let { scanner.stopScan(it) }
        } catch (_: Exception) {
            // Some devices throw if we stop with a different callback instance.
        }
        scanCallback = null
    }

    @SuppressLint("MissingPermission")
    private fun connectGatt(device: BluetoothDevice, serviceUuid: UUID, commandCharUuid: UUID) {
        Log.i("Wristband", "connecting to ${device.address}")
        gatt = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            device.connectGatt(context, false, gattCallback(serviceUuid, commandCharUuid), BluetoothDevice.TRANSPORT_LE)
        } else {
            device.connectGatt(context, false, gattCallback(serviceUuid, commandCharUuid))
        }
    }

    private fun gattCallback(serviceUuid: UUID, commandCharUuid: UUID) = object : BluetoothGattCallback() {
        @SuppressLint("MissingPermission")
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            if (newState == BluetoothGatt.STATE_CONNECTED) {
                Log.i("Wristband", "connected; discovering services")
                gatt.discoverServices()
            } else {
                Log.i("Wristband", "disconnected")
                commandChar = null
                HudStore.update { it.copy(wristbandConnected = false) }
            }
        }

        @SuppressLint("MissingPermission")
        override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) {
            val service: BluetoothGattService? = gatt.getService(serviceUuid)
            val characteristic = service?.getCharacteristic(commandCharUuid)
            if (characteristic == null) {
                Log.w("Wristband", "missing command characteristic")
                HudStore.update { it.copy(wristbandConnected = false) }
                return
            }
            commandChar = characteristic
            HudStore.update { it.copy(wristbandConnected = true) }
            Log.i("Wristband", "ready")
        }
    }

    @SuppressLint("MissingPermission")
    fun send(patternId: Int, intensity0to1: Float, durationMs: Int) {
        val g = gatt ?: return
        val c = commandChar ?: return
        val intensity = (intensity0to1.coerceIn(0f, 1f) * 255f).toInt()
        val buf = ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN)
            .put(patternId.toByte())
            .put(intensity.toByte())
            .putShort(durationMs.coerceIn(0, 65535).toShort())
            .array()
        c.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
        c.value = buf
        g.writeCharacteristic(c)
    }
}
