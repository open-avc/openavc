/**
 * What a device's driver reads as on a device line.
 *
 * Product hardware's driver ID is "openavc_builtin_" and a 64-character hash
 * of the product's model (`model_driver_id` in core/installed_equipment.py,
 * pinned by tests/test_installed_driver_label.py), so it reads as "Product
 * hardware", the name the server gives that driver. A reference to another
 * product's hardware has the same shape and reads the same. Every other
 * driver reads by its ID.
 */
const PRODUCT_HARDWARE_ID = /^openavc_builtin_[0-9a-f]{64}$/;

export function driverLabel(driverId: string | null | undefined): string {
  const id = driverId ?? "";
  return PRODUCT_HARDWARE_ID.test(id) ? "Product hardware" : id;
}
