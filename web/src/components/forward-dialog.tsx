"use client"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"

/** Turning a relay on widens who can reach what; say so before doing it. */
export function ForwardDialog({
  device,
  open,
  onOpenChange,
  onConfirm,
}: {
  device: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>在 {device} 上开启中转？</AlertDialogTitle>
          <AlertDialogDescription>
            开启后，能连到 {device} 的设备可以经由它搜索和读取它的邻居设备上的会话，即使它们与那些设备之间没有 SSH
            信任。只在你希望这些设备互相可见时开启。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction
            data-testid="forward-confirm"
            onClick={() => {
              onOpenChange(false)
              onConfirm()
            }}
          >
            开启中转
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
