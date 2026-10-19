# 电动力学简明教程

# 目录

# 第一章 静电场

# 1.1 库仑定律

# 1.2 高斯定理

# 第二章 静磁场

# 前言

本书是为物理专业本科生编写的电动力学教材。全书共两章，主要讨论静电场与静磁场的基本规律。

# 第一章 静电场

静电场是静止电荷产生的场。本章从库仑定律出发，建立静电场的基本方程。

# 1.1 库仑定律

真空中两个静止点电荷之间的相互作用力由库仑定律给出：

$$
\boldsymbol{F} = \frac{1}{4\pi\varepsilon_0}\frac{q_1 q_2}{r^2}\hat{\boldsymbol{r}} \tag{1.1}
$$

其中 $\varepsilon_0$ 为真空介电常量，$\hat{\boldsymbol{r}}$ 为由 $q_1$ 指向 $q_2$ 的单位矢量。

**定义 1.1（电场强度）** 单位正试探电荷在空间某点所受的力称为该点的电场强度，即 $\boldsymbol{E} = \boldsymbol{F}/q_0$。

电动力学简明教程

12

![](images/fig1_1.jpg)

图 1.1 点电荷的电场线分布

由叠加原理，点电荷系在空间某点产生的电场等于各点电荷单独存在时产生的电场的矢量和：

$$
\boldsymbol{E} = \sum_i \frac{1}{4\pi\varepsilon_0}\frac{q_i}{r_i^2}\hat{\boldsymbol{r}}_i \qquad (1.2)
$$

表 1.1 常用物理常量

<table><tr><td>常量</td><td>数值</td></tr><tr><td>$\varepsilon_0$</td><td>$8.85\times10^{-12}$ F/m</td></tr></table>

例 1.1 求半径为 $R$、带电量为 $Q$ 的均匀带电球面在球外一点产生的电场。

解 由对称性，电场沿径向，大小只与 $r$ 有关。取半径为 $r$ 的同心球面为高斯面，得

$$
E = \frac{Q}{4\pi\varepsilon_0 r^2}
$$

电动力学简明教程

13

# 1.2 高斯定理

**定理 1.2（高斯定理）** 通过任意闭合曲面的电通量等于该曲面所包围的总电荷除以 $\varepsilon_0$：

$$
\oint_S \boldsymbol{E}\cdot \mathrm{d}\boldsymbol{S} = \frac{Q}{\varepsilon_0} \tag{1.3}
$$

证明：考虑点电荷 $q$ 位于曲面内部的情形。由立体角的定义，$\mathrm{d}\Omega = \hat{\boldsymbol{r}}\cdot\mathrm{d}\boldsymbol{S}/r^2$，对整个闭合曲面积分得 $4\pi$，因此通量为 $q/\varepsilon_0$。由叠加原理即得一般情形。□

利用散度定理，高斯定理可以写成微分形式

$$
\nabla \cdot \boldsymbol{E} = \frac{\rho}{\varepsilon_0} \tag{1.4}
$$

这是静电场的基本方程之一。注意式中的 $\rho$ 为电荷体密度，当电荷分布具有足够对称性时，利用高斯定理可以方便地求出电场，这一方法在后续的许多问题中都会反复用到，是

求解静电问题的重要方法。

电动力学简明教程

14

# 习题

1. 两个点电荷 $q_1=2\,\mu\mathrm{C}$ 和 $q_2=-3\,\mu\mathrm{C}$ 相距 $10\,\mathrm{cm}$，求它们之间的相互作用力。

2. 利用高斯定理求无限长均匀带电直线的电场 $E = \frac{\lambda}{2\pi\varepsilon_0 r$。

# 第二章 静磁场

恒定电流产生的磁场称为静磁场。

# 2.1 毕奥-萨伐尔定律

电流元 $I\mathrm{d}\boldsymbol{l}$ 在空间某点产生的磁感应强度为

$$
\mathrm{d}\boldsymbol{B} = \frac{\mu_0}{4\pi}\frac{I\mathrm{d}\boldsymbol{l}\times\hat{\boldsymbol{r}}}{r^2} \tag{2.1}
$$

例 2.1 求无限长直导线电流 $I$ 在距导线 $a$ 处的磁感应强度。

解 由毕奥-萨伐尔定律积分可得 $B = \mu_0 I/(2\pi a)$，方向由右手定则确定。

电动力学简明教程

15

# 2.3 安培环路定理

**定理 2.1（安培环路定理）** 磁感应强度沿任意闭合回路的环量等于 $\mu_0$ 乘以穿过回路的电流：

$$
\oint_L \boldsymbol{B}\cdot\mathrm{d}\boldsymbol{l} = \mu_0 I \tag{2.2}
$$

![](images/fig2_1.jpg)

图 2-1 安培环路示意图

电动力学简明教程

16

# 参考文献

[1] 郭硕鸿. 电动力学. 高等教育出版社.
