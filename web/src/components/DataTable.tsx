import { useState } from "react";
import {
  flexRender, getCoreRowModel, getPaginationRowModel, getSortedRowModel,
  useReactTable, type ColumnDef, type SortingState,
} from "@tanstack/react-table";

export function DataTable<T>({ rows, columns, label }: {
  rows: T[]; columns: ColumnDef<T>[]; label: string;
}) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const table = useReactTable({
    data: rows, columns, state: { sorting }, onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(), initialState: { pagination: { pageSize: 20 } },
  });
  if (!rows.length) return <p className="muted">No {label.toLowerCase()} for these filters.</p>;
  return <>
    <div className="table-wrap">
      <table aria-label={label}>
        <thead>{table.getHeaderGroups().map(group => <tr key={group.id}>
          {group.headers.map(header => <th key={header.id} aria-sort={
            header.column.getIsSorted() === "asc" ? "ascending" : header.column.getIsSorted() === "desc" ? "descending" : "none"
          }>
            <button type="button" disabled={!header.column.getCanSort()} onClick={header.column.getToggleSortingHandler()}>
              {flexRender(header.column.columnDef.header, header.getContext())}
              {header.column.getIsSorted() === "asc" ? " ↑" : header.column.getIsSorted() === "desc" ? " ↓" : ""}
            </button>
          </th>)}
        </tr>)}</thead>
        <tbody>{table.getRowModel().rows.map(row => <tr key={row.id}>
          {row.getVisibleCells().map(cell => <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}
        </tr>)}</tbody>
      </table>
    </div>
    {rows.length > 20 && <div className="pager" aria-label={`${label} pagination`}>
      <button onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}>Previous</button>
      <span>Page {table.getState().pagination.pageIndex + 1} of {table.getPageCount()}</span>
      <button onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>Next</button>
    </div>}
  </>;
}
