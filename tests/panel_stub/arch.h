#define ARCH_SLEEP_OFF 0
#define ARCH_EXT_SLEEP_ON 1
static inline void arch_set_sleep_mode(int mode){(void)mode;}
static inline void arch_asm_delay_us(unsigned us){(void)us;}
