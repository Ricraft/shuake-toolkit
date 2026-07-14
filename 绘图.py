import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

# Define the data
data_main = {
    "位置x(cm)": [20.00, 22.50, 25.00, 27.50, 30.00, 32.50, 35.00, 37.50],
    "半径R(cm)": [5.50, 6.75, 8.00, 9.25, 10.50, 11.75, 13.00, 14.25],
    "PC(MeV)": [1.007, 1.236, 1.465, 1.694, 1.923, 2.152, 2.381, 2.610],
    "道址N": [194, 279, 363, 448, 531, 616, 701, 786],
    "测量能量E测(MeV)": [0.449, 0.633, 0.815, 0.999, 1.179, 1.363, 1.547, 1.731],
    "修正能量E修(MeV)": [0.219, 0.403, 0.585, 0.769, 0.949, 1.133, 1.317, 1.501],
    "相对论理论动能E理(MeV)": [0.619, 0.827, 1.041, 1.259, 1.479, 1.701, 1.924, 2.148]
}

df_main = pd.DataFrame(data_main)

data_calib = {
    "能量E(MeV)": [0.661, 1.17, 1.33],
    "道址N": [292, 531, 601]
}
df_calib = pd.DataFrame(data_calib)

constants = {
    "项目": ["磁场B(T)", "能量修正ΔE(MeV)", "电子静能m₀c²(MeV)", "定标公式", "PC计算公式", "相对论动能公式"],
    "数值/公式": [0.061049, 0.23, 0.511, "E=0.002165*N+0.0288", "PC=0.3×B×R", "E=√(PC²+(m₀c²)²)−m₀c²"]
}
df_const = pd.DataFrame(constants)

# Generate plots using matplotlib
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']  # support chinese
plt.rcParams['axes.unicode_minus'] = False

# Plot 1: Channel (道址N) vs Energy
fig, ax1 = plt.subplots(figsize=(8, 5))
ax1.plot(df_main["道址N"], df_main["测量能量E测(MeV)"], 'o-', label="测量能量 $E_{测}$", color='blue')
ax1.plot(df_main["道址N"], df_main["修正能量E修(MeV)"], 's-', label="修正能量 $E_{修}$", color='green')
ax1.plot(df_main["道址N"], df_main["相对论理论动能E理(MeV)"], '^--', label="相对论理论动能 $E_{理}$", color='red')
ax1.set_xlabel("道址 N (Channel)", fontsize=12)
ax1.set_ylabel("能量 Energy (MeV)", fontsize=12)
ax1.set_title("能量 vs 道址N (Energy vs Channel)", fontsize=14)
ax1.grid(True, linestyle='--', alpha=0.6)
ax1.legend(loc='upper left')
plt.tight_layout()
plt.savefig("energy_vs_channel.png", dpi=300)
plt.close()

# Plot 2: PC vs Energy
fig, ax2 = plt.subplots(figsize=(8, 5))
ax2.plot(df_main["PC(MeV)"], df_main["测量能量E测(MeV)"], 'o-', label="测量能量 $E_{测}$", color='blue')
ax2.plot(df_main["PC(MeV)"], df_main["修正能量E修(MeV)"], 's-', label="修正能量 $E_{修}$", color='green')
ax2.plot(df_main["PC(MeV)"], df_main["相对论理论动能E理(MeV)"], '^--', label="相对论理论动能 $E_{理}$", color='red')
ax2.set_xlabel("PC (MeV)", fontsize=12)
ax2.set_ylabel("能量 Energy (MeV)", fontsize=12)
ax2.set_title("能量 vs PC (Energy vs PC)", fontsize=14)
ax2.grid(True, linestyle='--', alpha=0.6)
ax2.legend(loc='upper left')
plt.tight_layout()
plt.savefig("energy_vs_pc.png", dpi=300)
plt.close()

# Create Excel File with beautiful styles
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "实验数据"

# Styles
font_title = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
font_data = Font(name="Calibri", size=11)
fill_title = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
fill_header = PatternFill(start_color="2F5597", end_color="2F5597", fill_type="solid")
fill_zebra = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")

thin_side = Side(border_style="thin", color="D9D9D9")
border_cell = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

align_center = Alignment(horizontal="center", vertical="center")
align_left = Alignment(horizontal="left", vertical="center")

# Write Main Data Table
ws.merge_cells("A1:G1")
ws["A1"] = "主实验数据表"
ws["A1"].font = font_title
ws["A1"].fill = fill_title
ws["A1"].alignment = align_center

headers_main = list(df_main.columns)
for col_num, header in enumerate(headers_main, 1):
    cell = ws.cell(row=2, column=col_num)
    cell.value = header
    cell.font = font_header
    cell.fill = fill_header
    cell.alignment = align_center

for row_num, row_data in enumerate(df_main.values, 3):
    for col_num, value in enumerate(row_data, 1):
        cell = ws.cell(row=row_num, column=col_num)
        cell.value = value
        cell.font = font_data
        cell.border = border_cell
        cell.alignment = align_center
        if row_num % 2 == 0:
            cell.fill = fill_zebra

# Write Calibration Table (leave 2 blank columns, so column I and J)
ws.merge_cells("I1:J1")
ws["I1"] = "定标数据表"
ws["I1"].font = font_title
ws["I1"].fill = fill_title
ws["I1"].alignment = align_center

headers_calib = list(df_calib.columns)
for col_num, header in enumerate(headers_calib, 9):
    cell = ws.cell(row=2, column=col_num)
    cell.value = header
    cell.font = font_header
    cell.fill = fill_header
    cell.alignment = align_center

for row_num, row_data in enumerate(df_calib.values, 3):
    for col_num, value in enumerate(row_data, 9):
        cell = ws.cell(row=row_num, column=col_num)
        cell.value = value
        cell.font = font_data
        cell.border = border_cell
        cell.alignment = align_center

# Write Constants & Formulas (Column L and M)
ws.merge_cells("L1:M1")
ws["L1"] = "常数与公式"
ws["L1"].font = font_title
ws["L1"].fill = fill_title
ws["L1"].alignment = align_center

for row_num, row_data in enumerate(df_const.values, 2):
    for col_num, value in enumerate(row_data, 12):
        cell = ws.cell(row=row_num, column=col_num)
        cell.value = value
        cell.font = font_data
        cell.border = border_cell
        if col_num == 12:
            cell.alignment = align_left
            cell.font = Font(name="Calibri", size=11, bold=True)
        else:
            cell.alignment = align_left

# Add Images to Excel
img1 = openpyxl.drawing.image.Image("energy_vs_channel.png")
img2 = openpyxl.drawing.image.Image("energy_vs_pc.png")

# Scale images to fit nicely in excel
img1.width, img1.height = 500, 312
img2.width, img2.height = 500, 312

ws.add_image(img1, "A13")
ws.add_image(img2, "I13")

# Adjust column widths
for col in ws.columns:
    max_len = max(len(str(cell.value or '')) for cell in col)
    col_letter = openpyxl.utils.get_column_letter(col[0].column)
    ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

wb.save("实验数据与图表.xlsx")
print("Successfully generated Excel and plots.")