      *================================================================*
      * INTPARM - PARAMETER BLOCK FOR CALL 'INTCALC'                   *
      *   METHOD S = SIMPLE ACTUAL/365 (ROUNDED, SIZE ERROR CHECKED)   *
      *          M = 30/360 (TRUNCATED)                                *
      *          C = DAILY COMPOUNDING (MAX 3660 DAYS)                 *
      *================================================================*
       01  INT-PARMS.
           05 IP-PRINCIPAL         PIC S9(9)V99 COMP-3.
           05 IP-ANNUAL-RATE       PIC 9V9(5) COMP-3.
           05 IP-START-DATE        PIC 9(8).
           05 IP-END-DATE          PIC 9(8).
           05 IP-METHOD            PIC X.
           05 IP-INTEREST          PIC S9(7)V99 COMP-3.
           05 IP-DAYS              PIC S9(5) COMP-3.
           05 IP-RETURN-CODE       PIC 99.
